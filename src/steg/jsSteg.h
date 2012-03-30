#ifndef _JSSTEG_H
#define _JSSTEG_H


#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include "util.h"
#include "connections.h"
#include "steg.h"
#include <event2/buffer.h>
#include "zpack.h"

// error codes
#define INVALID_BUF_SIZE	-1
#define INVALID_DATA_CHAR	-2

// controlling content gzipping for jsSteg
#define JS_GZIP_RESP             1

struct payloads;

int encodeHTTPBody(char *data, char *jTemplate,  char *jData,unsigned int dlen, 
		   unsigned int jtlen, unsigned int jdlen, int mode);

int isxString(char *str);

int isGzipContent (char *msg); 

int findContentType (char *msg);

int decodeHTTPBody (char *jData, char *dataBuf, unsigned int jdlen,
		    unsigned int dataBufSize, int *fin, int mode);

int encode(char *data, char *jTemplate, char *jData,
	   unsigned int dlen, unsigned int jtlen, unsigned int jdlen );

int encode2(char *data, char *jTemplate, char *jData,
	     unsigned int dlen, unsigned int jtlen,
	     unsigned int jdlen, int *fin);

int decode (char *jData, char *dataBuf, unsigned int jdlen,
	    unsigned int dlen, unsigned int dataBufSize );

int decode2 (char *jData, char *dataBuf, unsigned int jdlen,
	     unsigned int dataBufSize, int *fin );

void printerr(int errno);

int testEncode(char *data, char *js, char *outBuf, unsigned int dlen, unsigned int jslen, 
	       unsigned int outBufLen, int testNum);

int testDecode(char *inBuf, char *outBuf, unsigned int inBufSize, unsigned int dlen, 
	       unsigned int outBufSize, int testNum);

int testEncode2(char *data, char *js, char *outBuf, 
		unsigned int dlen, unsigned int jslen, unsigned int outBufLen,
		int mode, int testNum);

int testDecode2(char *inBuf, char *outBuf, 
	     unsigned int inBufSize, unsigned int outBufSize,
		int mode, int testNum);


int 
http_server_JS_transmit (payloads& pl, struct evbuffer *source, conn_t *conn, unsigned int content_type);

int
http_handle_client_JS_receive(steg_t *s, conn_t *conn, struct evbuffer *dest, struct evbuffer* source);



#endif


