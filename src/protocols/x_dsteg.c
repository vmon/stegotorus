/* Copyright 2011 Nick Mathewson, George Kadianakis
   See LICENSE for other credits and copying information
*/

#include "util.h"

#define PROTOCOL_X_DSTEG_PRIVATE
#include "x_dsteg.h"

#include <event2/buffer.h>

/* type-safe downcast wrappers */
static inline x_dsteg_config_t *
downcast_config(config_t *p)
{
  return DOWNCAST(x_dsteg_config_t, super, p);
}

static inline x_dsteg_conn_t *
downcast_conn(conn_t *p)
{
  return DOWNCAST(x_dsteg_conn_t, super, p);
}

/**
   Helper: Parses 'options' and fills 'cfg'.
*/
static int
parse_and_set_options(int n_options, const char *const *options,
                      x_dsteg_config_t *cfg)
{
  const char* defport;
  int req_options;

  if (n_options < 1)
    return -1;

  if (!strcmp(options[0], "client")) {
    defport = "48988"; /* bf5c */
    cfg->mode = LSN_SIMPLE_CLIENT;
    req_options = 4;
  } else if (!strcmp(options[0], "socks")) {
    defport = "23548"; /* 5bf5 */
    cfg->mode = LSN_SOCKS_CLIENT;
    req_options = 3;
  } else if (!strcmp(options[0], "server")) {
    defport = "11253"; /* 2bf5 */
    cfg->mode = LSN_SIMPLE_SERVER;
    req_options = 3;
  } else
    return -1;

  if (n_options != req_options)
      return -1;

  cfg->listen_addr = resolve_address_port(options[1], 1, 1, defport);
  if (!cfg->listen_addr)
    return -1;

  if (cfg->mode != LSN_SOCKS_CLIENT) {
    cfg->target_addr = resolve_address_port(options[2], 1, 0, NULL);
    if (!cfg->target_addr)
      return -1;
  }

  if (cfg->mode != LSN_SIMPLE_SERVER) {
    cfg->stegname = options[cfg->mode == LSN_SOCKS_CLIENT ? 2 : 3];
    if (!is_supported_steg(cfg->stegname))
      return -1;
  }

  return 0;
}

/* Deallocate 'cfg'. */
static void
x_dsteg_config_free(config_t *c)
{
  x_dsteg_config_t *cfg = downcast_config(c);
  if (cfg->listen_addr)
    evutil_freeaddrinfo(cfg->listen_addr);
  if (cfg->target_addr)
    evutil_freeaddrinfo(cfg->target_addr);
  free(cfg);
}

/**
   Populate 'cfg' according to 'options', which is an array like this:
   {"socks","127.0.0.1:6666"}
*/
static config_t *
x_dsteg_config_create(int n_options, const char *const *options)
{
  x_dsteg_config_t *cfg = xzalloc(sizeof(x_dsteg_config_t));
  cfg->super.vtable = &x_dsteg_vtable;

  if (parse_and_set_options(n_options, options, cfg) == 0)
    return &cfg->super;

  x_dsteg_config_free(&cfg->super);
  log_warn("x_dsteg syntax:\n"
           "\tx_dsteg <mode> <listen_address> [<target_address>] [<steg>]\n"
           "\t\tmode ~ server|client|socks\n"
           "\t\tlisten_address, target_address ~ host:port\n"
           "\t\tsteg ~ steganography module name\n"
           "\ttarget_address is required for server and client mode,\n"
           "\tand forbidden for socks mode.\n"
           "\tsteg is required for client and socks mode,\n"
           "\tforbidden for server.\n"
           "Examples:\n"
           "\tobfsproxy x_dsteg socks 127.0.0.1:5000 x_http\n"
           "\tobfsproxy x_dsteg client 127.0.0.1:5000 192.168.1.99:11253 x_http\n"
           "\tobfsproxy x_dsteg server 192.168.1.99:11253 127.0.0.1:9005");
  return NULL;
}

/** Retrieve the 'n'th set of listen addresses for this configuration. */
static struct evutil_addrinfo *
x_dsteg_config_get_listen_addrs(config_t *cfg, size_t n)
{
  if (n > 0)
    return 0;
  return downcast_config(cfg)->listen_addr;
}

/* Retrieve the target address for this configuration. */
static struct evutil_addrinfo *
x_dsteg_config_get_target_addr(config_t *cfg)
{
  return downcast_config(cfg)->target_addr;
}

/*
  This is called everytime we get a connection for the x_dsteg
  protocol.
*/

static conn_t *
x_dsteg_conn_create(config_t *c)
{
  x_dsteg_config_t *cfg = downcast_config(c);
  x_dsteg_conn_t *conn = xzalloc(sizeof(x_dsteg_conn_t));
  conn->super.cfg = c;
  conn->super.mode = cfg->mode;
  if (conn->super.mode != LSN_SIMPLE_SERVER) {
    conn->steg = steg_new(cfg->stegname);
    if (!conn->steg) {
      free(conn);
      return 0;
    }
  }
  return &conn->super;
}

static void
x_dsteg_conn_free(conn_t *c)
{
  x_dsteg_conn_t *conn = downcast_conn(c);
  if (conn->steg)
    steg_del(conn->steg);
  free(conn);
}

/** Dsteg has no handshake */
static int
x_dsteg_handshake(conn_t *conn)
{
  return 0;
}

/** XXX ignores transmit_room */
static int
x_dsteg_send(conn_t *d, struct evbuffer *source)
{
  x_dsteg_conn_t *dest = downcast_conn(d);
  obfs_assert(dest->steg);
  return steg_transmit(dest->steg, source, d);
}

static enum recv_ret
x_dsteg_recv(conn_t *s, struct evbuffer *dest)
{
  x_dsteg_conn_t *source = downcast_conn(s);
  if (!source->steg) {
    obfs_assert(source->super.mode == LSN_SIMPLE_SERVER);
    source->steg = steg_detect(s);
    if (!source->steg) {
      log_debug("No recognized steg pattern detected");
      return RECV_BAD;
    } else {
      log_debug("Detected steg pattern %s", source->steg->vtable->name);
    }
  }
  return steg_receive(source->steg, s, dest);
}

/** XXX all steg callbacks are ignored */
static void x_dsteg_expect_close(conn_t *conn) {}
static void x_dsteg_cease_transmission(conn_t *conn) {}
static void x_dsteg_close_after_transmit(conn_t *conn) {}
static void x_dsteg_transmit_soon(conn_t *conn, unsigned long timeout) {}

DEFINE_PROTOCOL_VTABLE_STEG(x_dsteg);