#pragma once

// Required by WebRTC DTLS-SRTP key negotiation. Mbed TLS keeps this disabled
// in its generic default configuration.
#define MBEDTLS_SSL_DTLS_SRTP
