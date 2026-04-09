#!/bin/bash

docker stop workout-tracker
docker rm workout-tracker
docker build -t workout-tracker .
docker run -d --name workout-tracker --restart unless-stopped   -p 127.0.0.1:8000:8000   -v workout-data:/app/data   --env-file .env   workout-tracker
