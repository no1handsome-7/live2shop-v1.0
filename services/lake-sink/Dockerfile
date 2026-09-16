FROM python:3.11-slim
WORKDIR /app
COPY services/lake-sink/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/lake-sink/main.py .
CMD ["python","main.py"]
