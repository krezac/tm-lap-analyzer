FROM python:3.8.0-alpine3.10

EXPOSE 8000
ADD src /src

RUN apk add --no-cache --virtual .build-deps \
    build-base freetype-dev \
    gcc \
    python3-dev \
    musl-dev \
    postgresql-dev \
    && pip install --no-cache-dir psycopg2 numpy gpxplotter pendulum Jinja2 

RUN apk add --no-cache libpq freetype

CMD [ "python", "./src/server.py" ]

