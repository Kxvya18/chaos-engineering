# Helper image for the network-delay fault: only iproute2 (tc). Runs with NET_ADMIN
# inside the *target's* network namespace; the target itself gains no capabilities.
FROM alpine:3.20
RUN apk add --no-cache iproute2
