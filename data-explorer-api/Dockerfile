FROM rust:1.85-bookworm AS builder
WORKDIR /app

COPY Cargo.toml ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs && cargo build --release && rm -rf src target/release/deps/data_explorer_api*

COPY src ./src
RUN touch src/main.rs && cargo build --release

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/data-explorer-api /usr/local/bin/data-explorer-api

EXPOSE 8080
CMD ["data-explorer-api"]
