ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} \
    numpy==2.3.5 scipy==1.16.3 matplotlib==3.10.8 pandas==2.3.3 seaborn==0.13.2
ENV MPLBACKEND=Agg OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
USER 65534:65534
WORKDIR /tmp
