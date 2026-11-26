FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY v2 ./v2
COPY v4 ./v4
RUN pip install --no-cache-dir -r v4/requirements.txt
EXPOSE 8503
CMD ["uvicorn", "v4.main:app", "--host", "0.0.0.0", "--port", "8503"]
