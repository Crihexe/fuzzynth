FROM scratch

COPY rootfs /

ENV LD_LIBRARY_PATH=/opt/fuzzynth
ENV ASAN_SYMBOLIZER_PATH=/opt/fuzzynth/llvm-symbolizer
ENV ASAN_OPTIONS=abort_on_error=1:symbolize=1
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["/opt/fuzzynth/d8"]
