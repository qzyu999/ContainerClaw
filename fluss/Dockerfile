FROM python:3.11-slim-bookworm

WORKDIR /app
RUN pip install flask

COPY src/ ./src/

EXPOSE 9092

CMD ["python", "src/main.py"]
