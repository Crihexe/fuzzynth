FROM scratch

COPY rootfs /

ENV LD_LIBRARY_PATH=/opt/fuzzynth
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["/opt/fuzzynth/d8"]
