FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python -m scripts.train --users 150 --behaviour-users 40
EXPOSE 8000
CMD ["uvicorn", "api.service:app", "--host", "0.0.0.0", "--port", "8000"]
