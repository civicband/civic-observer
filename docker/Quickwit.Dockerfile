FROM debian:bookworm-slim

ENV QW_VERSION=0.9.0-rc

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/* && \
    # Force nsswitch to check /etc/hosts before DNS
    echo 'hosts: files dns mymachines' > /etc/nsswitch.conf

RUN curl -fSL -H "User-Agent: Mozilla/5.0" -L "https://github.com/quickwit-oss/quickwit/releases/download/v${QW_VERSION}/quickwit-v${QW_VERSION}-aarch64-unknown-linux-gnu.tar.gz" -o /tmp/quickwit.tar.gz && \
    tar -xzf /tmp/quickwit.tar.gz && \
    rm /tmp/quickwit.tar.gz

ENV QW_HOME=/opt/quickwit
RUN mkdir -p /opt/quickwit && \
    cp -r /opt/quickwit-v*/* /opt/quickwit/ 2>/dev/null || true
RUN ln -sf ${QW_HOME}/quickwit /usr/local/bin/quickwit

WORKDIR ${QW_HOME}
ENV PATH="${QW_HOME}:${PATH}"
ENV QW_DATA_DIR="${QW_HOME}/qwdata"

EXPOSE 7280 7281

CMD ["quickwit", "run"]
