/* Copyright 2011 Nick Mathewson, George Kadianakis
   See LICENSE for other credits and copying information
*/

#ifndef CRYPT_H
#define CRYPT_H

#include <stddef.h> /* for size_t */

#define SHA256_LENGTH 32

/* Stream cipher state */
typedef struct crypt_t crypt_t;
/* Digest state */
typedef struct digest_t digest_t;

typedef unsigned char uchar;

/** Initialize global crypto state.  Returrn 0 on success, -1 on failure */
int initialize_crypto(void);
/** Clean up global crypto state */
void cleanup_crypto(void);

/** Return a newly allocated digest state, or NULL on failure. */
digest_t *digest_new(void);
/** Add n bytes from b to the digest state. */
void digest_update(digest_t *, const uchar *b, size_t n);
/** Get a digest from the digest state.  Put it in up the first n bytes of the
buffer b.  Return the number of bytes actually written.*/
size_t digest_getdigest(digest_t *, uchar *b, size_t n);
/** Clear and free a digest state */
void digest_free(digest_t *);

/** Return a new stream cipher state taking key and IV from the data provided.
 * The data length must be exactly 32 */
crypt_t *crypt_new(const uchar *, size_t);
void crypt_set_iv(crypt_t *key, const uchar *iv, size_t ivlen);

/** Encrypt n bytes of data in the buffer b, in place. */
void stream_crypt(crypt_t *, uchar *b, size_t n);
/** Clear and free a stream cipher state. */
void crypt_free(crypt_t *);

/** Set b to contain n random bytes. */
int random_bytes(uchar *b, size_t n);

#ifdef CRYPT_PRIVATE

#include <openssl/aes.h>

/* ==========
   These definitions are not part of the crypt interface.
   They're exposed here so that the unit tests can use them.
   ==========
*/
struct crypt_t {
  AES_KEY key;
  uchar ivec[AES_BLOCK_SIZE];
  uchar ecount_buf[AES_BLOCK_SIZE];
  unsigned int pos;
};
#endif

#endif
