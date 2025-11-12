FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY nivel4.py .

# Se expone el puerto 8080 que usará Cloud Run
EXPOSE 8080

CMD ["uvicorn", "nivel4:app", "--host", "0.0.0.0", "--port", "8080"]