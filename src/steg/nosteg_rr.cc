/*  Copyright (c) 2011, SRI International

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

    * Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.

    * Redistributions in binary form must reproduce the above
copyright notice, this list of conditions and the following disclaimer
in the documentation and/or other materials provided with the
distribution.

    * Neither the names of the copyright owners nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

    Contributors: Zack Weinberg, Vinod Yegneswaran
    See LICENSE for other credits and copying information
*/

#include "util.h"
#include "connections.h"
#include "protocol.h"
#include "steg.h"
#include <event2/buffer.h>

namespace {
  struct nosteg_rr_steg_config_t : steg_config_t
  {
    STEG_CONFIG_DECLARE_METHODS(nosteg_rr);
  };

  struct nosteg_rr_steg_t : steg_t
  {
    nosteg_rr_steg_config_t *config;
    conn_t *conn;

    bool can_transmit : 1;
    bool did_transmit : 1;

    nosteg_rr_steg_t(nosteg_rr_steg_config_t *cf, conn_t *cn);
    STEG_DECLARE_METHODS(nosteg_rr);
  };
}

STEG_DEFINE_MODULE(nosteg_rr);

nosteg_rr_steg_config_t::nosteg_rr_steg_config_t(config_t *cfg)
  : steg_config_t(cfg)
{
}

nosteg_rr_steg_config_t::~nosteg_rr_steg_config_t()
{
}

steg_t *
nosteg_rr_steg_config_t::steg_create(conn_t *conn)
{
  return new nosteg_rr_steg_t(this, conn);
}

nosteg_rr_steg_t::nosteg_rr_steg_t(nosteg_rr_steg_config_t *cf,
                                   conn_t *cn)
  : config(cf), conn(cn),
    can_transmit(cf->cfg->mode != LSN_SIMPLE_SERVER),
    did_transmit(false)
{
}

nosteg_rr_steg_t::~nosteg_rr_steg_t()
{
}

steg_config_t *
nosteg_rr_steg_t::cfg()
{
  return config;
}

size_t
nosteg_rr_steg_t::transmit_room(size_t pref, size_t, size_t)
{
  return can_transmit ? pref : 0;
}

int
nosteg_rr_steg_t::transmit(struct evbuffer *source)
{
  log_assert(can_transmit);

  struct evbuffer *dest = conn->outbound();

  log_debug(conn, "transmitting %lu bytes",
            (unsigned long)evbuffer_get_length(source));

  if (evbuffer_add_buffer(dest, source)) {
    log_warn(conn, "failed to transfer buffer");
    return -1;
  }

  did_transmit = true;
  can_transmit = false;
  conn->cease_transmission();

  return 0;
}

int
nosteg_rr_steg_t::receive(struct evbuffer *dest)
{
  struct evbuffer *source = conn->inbound();

  log_debug(conn, "%s-side receiving %lu bytes",
            config->cfg->mode == LSN_SIMPLE_SERVER ? "server" : "client",
            (unsigned long)evbuffer_get_length(source));

  if (evbuffer_add_buffer(dest, source)) {
    log_warn(conn, "failed to transfer buffer");
    return -1;
  }

  if (config->cfg->mode != LSN_SIMPLE_SERVER) {
    conn->expect_close();
  } else if (!did_transmit) {
    can_transmit = true;
    conn->transmit_soon(100);
  }

  return 0;
}
