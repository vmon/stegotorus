#! @PYTHON@

# Integration tests for obfsproxy.
#
# The obfsproxy binary is assumed to exist in the current working
# directory, and you need to have Python 2.6 or better (but not 3).
# You need to be able to make connections to arbitrary high-numbered
# TCP ports on the loopback interface.

import difflib
import errno
import Queue
import os
import re
import signal
import socket
import struct
import subprocess
import threading
import time
import traceback
import unittest

# Helper: generate unified-format diffs between two named strings.
# Pythonic escaped-string syntax is used for unprintable characters.

def diff(label, expected, received):
    if expected == received:
        return ""
    else:
        return (label + "\n"
                + "\n".join(s.encode("string_escape")
                            for s in
                            difflib.unified_diff(expected.split("\n"),
                                                 received.split("\n"),
                                                 "expected", "received",
                                                 lineterm=""))
                + "\n")

# Helper: Run obfsproxy instances and confirm that they have
# completed without any errors.

class Obfsproxy(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        argv = ["./obfsproxy", "--log-min-severity=debug"]
        if len(args) == 1 and (isinstance(args[0], list) or
                               isinstance(args[0], tuple)):
            argv.extend(args[0])
        else:
            argv.extend(args)

        # Windows needs an extra argument passed to parent __init__,
        # but the constant isn't defined on Unix.
        try: kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
        except AttributeError: pass
        subprocess.Popen.__init__(self, argv,
                                  stdin=open(os.path.devnull, "r"),
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  **kwargs)

    severe_error_re = re.compile(r"\[(?:warn|err(?:or)?)\]")

    def check_completion(self, label, force_stderr):
        if self.poll() is None:
            try: signo = signal.CTRL_C_EVENT # Windows
            except AttributeError: signo = signal.SIGINT # Unix

            # subprocess.communicate has no timeout; arrange to blow
            # the process away if it doesn't respond to the original
            # signal in a timely fashion.
            timeout = threading.Thread(target=self.stop, args=(1.0,))
            timeout.daemon = True
            timeout.start()
            self.send_signal(signo)

        (out, err) = self.communicate()

        report = ""
        def indent(s):
            return "| " + "\n| ".join(s.strip().split("\n"))

        # exit status should be zero
        if self.returncode > 0:
            report += label + " exit code: %d\n" % self.returncode
        elif self.returncode < 0:
            report += label + " killed: signal %d\n" % -self.returncode

        # there should be nothing on stdout
        if out != "":
            report += label + " stdout:\n%s\n" % indent(out)

        # there will be debugging messages on stderr, but there should be
        # no [warn], [err], or [error] messages.
        if (force_stderr or
            self.severe_error_re.search(err) or
            self.returncode != 0):
            report += label + " stderr:\n%s\n" % indent(err)

        return report

    def stop(self, delay=None):
        if self.poll() is None:
            if delay is not None:
                time.sleep(delay)
                if self.poll() is not None: return
            self.terminate()

# Helper: Repeatedly try to connect to the specified server socket
# until either it succeeds or one full second has elapsed.  (Surely
# there is a better way to do this?)

def connect_with_retry(addr):
    retry = 0
    while True:
        try:
            return socket.create_connection(addr)
        except socket.error, e:
            if e.errno != errno.ECONNREFUSED: raise
            if retry == 20: raise
            retry += 1
            time.sleep(0.05)

# Helper: In a separate thread (to avoid deadlock), listen on a
# specified socket.  The first time something connects to that socket,
# read all available data, stick it in a string, and post the string
# to the output queue.  Then close both sockets and exit.

class ReadWorker(threading.Thread):
    def run(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.settimeout(0.1)
        listener.bind(self.address)
        listener.listen(1)
        try:
            (conn, remote) = listener.accept()
        except Exception, e:
            data = "|ACCEPT ERROR: " + str(e)
            return
        if not self.running: return
        listener.close()
        conn.settimeout(0.1)
        data = ""
        try:
            while True:
                chunk = conn.recv(4096)
                if not self.running: raise socket.timeout
                if chunk == "": break
                data += chunk
        except Exception, e:
            data += "|RECV ERROR: " + str(e)
        conn.close()
        self.data = data

    def __init__(self, address):
        self.address = address
        self.data = ""
        self.running = True
        threading.Thread.__init__(self)
        self.start()

    def get(self):
        self.join(0.5)
        return self.data

    def stop(self):
        self.running = False
        self.join(0.5)

# Right now this is a direct translation of the former int_test.sh
# (except that I have fleshed out the SOCKS test a bit).
# It will be made more general and parametric Real Soon.

ENTRY_PORT  = 4999
SERVER_PORT = 5000
EXIT_PORT   = 5001

#
# Test base classes.  They do _not_ inherit from unittest.TestCase
# so that they are not scanned directly for test functions (some of
# them do provide test functions, but not in a usable state without
# further code from subclasses).
#

class DirectTest(object):
    def setUp(self):
        self.output_reader = ReadWorker(("127.0.0.1", EXIT_PORT))
        self.obfs = Obfsproxy(self.obfs_args)
        self.input_chan = connect_with_retry(("127.0.0.1", ENTRY_PORT))
        self.input_chan.settimeout(1.0)

    def tearDown(self):
        self.obfs.stop()
        self.output_reader.stop()
        self.input_chan.close()

    def test_direct_transfer(self):
        # Open a server and a simple client (in the same process) and
        # transfer a file.  Then check whether the output is the same
        # as the input.
        self.input_chan.sendall(TEST_FILE)
        self.input_chan.shutdown(socket.SHUT_WR)
        try:
            output = self.output_reader.get()
        except Queue.Empty:
            output = ""

        report = diff("errors in transfer:", TEST_FILE, output)

        report += self.obfs.check_completion("obfsproxy", report!="")

        if report != "":
            self.fail("\n" + report)

# Same as above, but we use a socks client instead of a simple client,
# and the server's a separate process.

class SocksTest(object):
    # 'sequence' is a sequence of SOCKS[45] protocol messages
    # which we will send or receive.  Sends alternate with
    # receives.  Each entry may be a string, which is sent or
    # received verbatim; a pair of a sequence of data items and a
    # struct pack code, which is packed and then sent or received;
    # or the constant False, which means the server is expected to
    # drop the connection at that point.  If we come to the end of
    # the SOCKS sequence without the server having dropped the
    # connection, we transmit the test file and expect to get it
    # back from the far end.
    def socksTestInner(self, sequence, input_chan):
        sending = True
        good = True
        for msg in sequence:
            if msg is False:
                input_chan.shutdown(socket.SHUT_WR)
                # Expect either a clean closedown or a connection reset
                # at this point.
                got = ""
                try:
                    got = input_chan.recv(4096)
                except socket.error, e:
                    if e.errno != errno.ECONNRESET: raise
                self.assertEqual(got, "")
                good = False
                break
            elif isinstance(msg, str):
                exp = msg
            elif isinstance(msg, tuple):
                exp = struct.pack(msg[1], *msg[0])
            else:
                raise TypeError("incomprehensible msg: " + repr(msg))
            if sending:
                input_chan.sendall(exp)
            else:
                got = ""
                try:
                    got = input_chan.recv(4096)
                except socket.error, e:
                    if e.errno != errno.ECONNRESET: raise
                self.assertEqual(got, exp)
            sending = not sending
        if not good: return None

        input_chan.sendall(TEST_FILE)
        input_chan.shutdown(socket.SHUT_WR)
        return self.output_reader.get()

    def socksTest(self, sequence):
        input_chan = connect_with_retry(("127.0.0.1", ENTRY_PORT))
        input_chan.settimeout(1.0)

        try:
            output = self.socksTestInner(sequence, input_chan)
            report = ""
        except Exception:
            output = None
            report = traceback.format_exc()

        input_chan.close()

        if output is not None:
            report += diff("errors in transfer:", TEST_FILE, output)

        fs = report != ""

        report += self.obfs_client.check_completion("obfsproxy client", fs)
        if self.obfs_server is not None:
            report += self.obfs_server.check_completion("obfsproxy server", fs)

        if report != "":
            self.fail("\n" + report)

class GoodSocksTest(SocksTest):
    # Test methods for good SOCKS dialogues; these should be repeated for each
    # protocol.
    def setUp(self):
        self.output_reader = ReadWorker(("127.0.0.1", EXIT_PORT))
        self.obfs_server = Obfsproxy(self.server_args)
        self.obfs_client = Obfsproxy(self.client_args)

    def tearDown(self):
        self.obfs_server.stop()
        self.obfs_client.stop()
        self.output_reader.stop()


    def test_socks4_transfer(self):
        # SOCKS4 connection request - should succeed
        self.socksTest([ ( (4, 1, SERVER_PORT, 127, 0, 0, 1, 0), "!BBH5B" ),
                         ( (0, 90, SERVER_PORT, 127, 0, 0, 1), "!BBH4B" ) ])

    def test_socks5_transfer(self):
        self.socksTest([ "\x05\x01\x00", "\x05\x00",
                         ( (5, 1, 0, 1, 127, 0, 0, 1, SERVER_PORT), "!8BH" ),
                         ( (5, 0, 0, 1, 127, 0, 0, 1, SERVER_PORT), "!8BH" ) ])

#
# Concrete test classes that are not protocol-specific.
#

class SocksBad(SocksTest, unittest.TestCase):
    # We never actually make a connection, so there's no point having a
    # server or an output reader.
    def setUp(self):
        self.obfs_client = Obfsproxy(self.client_args)
        self.obfs_server = None

    def tearDown(self):
        self.obfs_client.stop()

    client_args = ("dummy", "socks",
                   "127.0.0.1:%d" % ENTRY_PORT)

    def test_illformed(self):
        # ill-formed socks message - server should drop connection
        self.socksTest([ "GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                         "Connection: close\r\n\r\n",
                         False ])

    def test_socks4_unsupported_method_1(self):
        # SOCKS4 bind request - should fail, presently just drops connection
        self.socksTest([ ( (4, 2, SERVER_PORT, 127, 0, 0, 1, 0), "!BBH5B" ),
                         False ])

    def test_socks5_bad_handshake_1(self):
        self.socksTest([ "\x05", False ])

    def test_socks5_bad_handshake_2(self):
        self.socksTest([ "\x05\x00", False ])

    def test_socks5_bad_handshake_3(self):
        self.socksTest([ "\x05\x01\x01", False ]) # should get "\x05\xFF"

    def test_socks5_bad_handshake_4(self):
        self.socksTest([ "\x05\x01\x080", False ]) # should get "\x05\xFF"

    def test_socks5_bad_handshake_5(self):
        self.socksTest([ "\x05\x02\x01\x02", False ]) # should get "\x05\xFF"

    def test_socks5_good_handshake_1(self):
        self.socksTest([ "\x05\x01\x00", "\x05\x00", False ])

    def test_socks5_good_handshake_2(self):
        self.socksTest([ "\x05\x02\x00\x01", "\x05\x00", False ])

    def test_socks5_unsupported_method_1(self):
        self.socksTest([ "\x05\x01\x00", "\x05\x00",
                         ( (5, 2, 0, 1, 127, 0, 0, 1, SERVER_PORT), "!8BH" ),
                         "\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00", False ])

    def test_socks5_unsupported_method_2(self):
        self.socksTest([ "\x05\x01\x00", "\x05\x00",
                         ( (5, 3, 0, 1, 127, 0, 0, 1, SERVER_PORT), "!8BH" ),
                         "\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00", False ])



#
# Concrete test classes specialize the above base classes for each protocol.
#

# fails, disabled
#class DirectObfs2(DirectTest, unittest.TestCase):
#    obfs_args = ("obfs2",
#                 "--dest=127.0.0.1:%d" % EXIT_PORT,
#                 "server", "127.0.0.1:%d" % SERVER_PORT,
#                 "obfs2",
#                 "--dest=127.0.0.1:%d" % SERVER_PORT,
#                 "client", "127.0.0.1:%d" % ENTRY_PORT)

class DirectDummy(DirectTest, unittest.TestCase):
    obfs_args = ("dummy", "server",
                 "127.0.0.1:%d" % SERVER_PORT,
                 "127.0.0.1:%d" % EXIT_PORT,
                 "dummy", "client",
                 "127.0.0.1:%d" % ENTRY_PORT,
                 "127.0.0.1:%d" % SERVER_PORT)

class DirectXDstegXHttp(DirectTest, unittest.TestCase):
    obfs_args = ("x_dsteg", "server",
                 "127.0.0.1:%d" % SERVER_PORT,
                 "127.0.0.1:%d" % EXIT_PORT,
                 "x_dsteg", "client",
                 "127.0.0.1:%d" % ENTRY_PORT,
                 "127.0.0.1:%d" % SERVER_PORT, "x_http")

# fails, disabled
#class SocksObfs2(GoodSocksTest, unittest.TestCase):
#    server_args = ("obfs2",
#                   "--dest=127.0.0.1:%d" % EXIT_PORT,
#                   "server", "127.0.0.1:%d" % SERVER_PORT)
#    client_args = ("obfs2",
#                   "socks", "127.0.0.1:%d" % ENTRY_PORT)

class SocksDummy(GoodSocksTest, unittest.TestCase):
    server_args = ("dummy", "server",
                   "127.0.0.1:%d" % SERVER_PORT,
                   "127.0.0.1:%d" % EXIT_PORT)
    client_args = ("dummy", "socks",
                   "127.0.0.1:%d" % ENTRY_PORT)

class SocksXDstegXHttp(GoodSocksTest, unittest.TestCase):
    server_args = ("x_dsteg", "server",
                   "127.0.0.1:%d" % SERVER_PORT,
                   "127.0.0.1:%d" % EXIT_PORT)
    client_args = ("x_dsteg", "socks",
                   "127.0.0.1:%d" % ENTRY_PORT, "x_http")

TEST_FILE = """\
THIS IS A TEST FILE. IT'S USED BY THE INTEGRATION TESTS.
THIS IS A TEST FILE. IT'S USED BY THE INTEGRATION TESTS.
THIS IS A TEST FILE. IT'S USED BY THE INTEGRATION TESTS.
THIS IS A TEST FILE. IT'S USED BY THE INTEGRATION TESTS.

"Can entropy ever be reversed?"
"THERE IS AS YET INSUFFICIENT DATA FOR A MEANINGFUL ANSWER."
"Can entropy ever be reversed?"
"THERE IS AS YET INSUFFICIENT DATA FOR A MEANINGFUL ANSWER."
"Can entropy ever be reversed?"
"THERE IS AS YET INSUFFICIENT DATA FOR A MEANINGFUL ANSWER."
"Can entropy ever be reversed?"
"THERE IS AS YET INSUFFICIENT DATA FOR A MEANINGFUL ANSWER."
"Can entropy ever be reversed?"
"THERE IS AS YET INSUFFICIENT DATA FOR A MEANINGFUL ANSWER."
"Can entropy ever be reversed?"
"THERE IS AS YET INSUFFICIENT DATA FOR A MEANINGFUL ANSWER."
"Can entropy ever be reversed?"
"THERE IS AS YET INSUFFICIENT DATA FOR A MEANINGFUL ANSWER."
"Can entropy ever be reversed?"
"THERE IS AS YET INSUFFICIENT DATA FOR A MEANINGFUL ANSWER."

    In obfuscatory age geeky warfare did I wage
      For hiding bits from nasty censors' sight
    I was hacker to my set in that dim dark age of net
      And I hacked from noon till three or four at night

    Then a rival from Helsinki said my protocol was dinky
      So I flamed him with a condescending laugh,
    Saying his designs for stego might as well be made of lego
      And that my bikeshed was prettier by half.

    But Claude Shannon saw my shame. From his noiseless channel came
       A message sent with not a wasted byte
    "There are nine and sixty ways to disguise communiques
       And RATHER MORE THAN ONE OF THEM IS RIGHT"

		    (apologies to Rudyard Kipling.)
"""

if __name__ == '__main__':
    # Doesn't yet work correctly on Windows
    if os.name == 'posix':
        unittest.main()
    else:
        print "*** Integration tests skipped on Windows"
