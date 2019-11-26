FROM python:3.8.0-alpine3.10

EXPOSE 8000
ADD src /src

RUN apk add --no-cache --virtual .build-deps \
    gcc \
    python3-dev \
    musl-dev \
    postgresql-dev \
    && pip install --no-cache-dir psycopg2 numpy \
    && apk del --no-cache .build-deps

CMD [ "python", "./src/server.py" ]

