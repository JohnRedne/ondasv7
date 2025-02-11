# -*- coding: utf-8 -*-
"""Untitled42.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1oFay1t-JZr8mWiZ28bYAp06iP9e2M-gG
"""

from flask import Flask, request, jsonify, send_file
import requests
import io
from obspy import read, UTCDateTime
import matplotlib.pyplot as plt
import os
from flask_cors import CORS
from redis import Redis
from celery import Celery
from datetime import datetime, timedelta

# Configuración de la aplicación
app = Flask(__name__)
CORS(app)  # Habilitar CORS para evitar bloqueos en Flutter

# Configuración de Redis para Render
app.config['REDIS_URL'] = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
redis = Redis.from_url(app.config['REDIS_URL'])

# Configuración de Celery con Redis
app.config['CELERY_BROKER_URL'] = os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/0')
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

# Función para convertir una fecha en día juliano
def date_to_julian_day(date: datetime) -> int:
    """Convierte una fecha en el día juliano del año."""
    start_of_year = datetime(date.year, 1, 1)
    return (date - start_of_year).days + 1

# Ruta principal para verificar que el backend funciona
@app.route('/')
def home():
    return jsonify({"message": "El backend está funcionando correctamente"}), 200

# Tarea asincrónica de Celery para generar el sismograma
@celery.task(bind=True)
def generate_sismogram_task(self, start_date_input, end_date_input, net, sta):
    try:
        # Convertir fechas ISO8601 a formato datetime
        start_date = datetime.strptime(start_date_input, "%Y-%m-%dT%H:%M:%SZ")
        end_date = datetime.strptime(end_date_input, "%Y-%m-%dT%H:%M:%SZ")

        # Ajustar si las horas son iguales
        if start_date == end_date:
            end_date += timedelta(seconds=20)

        # Limitar el intervalo máximo a 15 minutos
        if (end_date - start_date) > timedelta(minutes=15):
            end_date = start_date + timedelta(minutes=15)

        # Convertir fecha de inicio a día juliano
        julian_day = date_to_julian_day(start_date)
        year = start_date.year

        # Construcción de la URL para OSSO
        channels = ["HNE.D", "HNN.D", "HNZ.D"]
        osso_urls = [
            f"http://osso.univalle.edu.co/apps/seiscomp/archive/{year}/{net}/{sta}/{channel}/{net}.{sta}.00.{channel}.{year}.{julian_day}"
            
            for channel in channels
        ]


        # Descargar y procesar los datos MiniSEED
        stream = None
        for osso_url in osso_urls:
            response = requests.get(osso_url, stream=True, timeout=500)
            if response.status_code == 200:
                temp_stream = read(io.BytesIO(response.content))
                if stream is None:
                    stream = temp_stream
                else:
                    stream += temp_stream  # Combinar streams si hay más de uno
            else:
                self.update_state(state='FAILURE', meta={'error': f"Error {response.status_code} al descargar MiniSEED desde {osso_url}."})
                return {"error": f"Error {response.status_code} al descargar MiniSEED desde {osso_url}."}

        # Recortar los datos
        start_utc = UTCDateTime(start_date.isoformat() + "Z")
        end_utc = UTCDateTime(end_date.isoformat() + "Z")
        stream = stream.slice(starttime=start_utc, endtime=end_utc)

        # Graficar el sismograma
        fig, ax = plt.subplots(figsize=(12, 6))
        for trace in stream:
            ax.plot(trace.times("matplotlib"), trace.data, label=f"{trace.stats.channel}", linewidth=0.8)

        ax.set_title(f"Sismograma ({sta})", fontsize=12)
        ax.set_xlabel("Tiempo (HH:MM:SS UTC)", fontsize=10)
        ax.set_ylabel("Amplitud", fontsize=10)
        ax.legend(loc="upper right")
        ax.grid(True, linestyle="--", alpha=0.7)

        # Guardar la imagen en memoria
        output_image = io.BytesIO()
        plt.savefig(output_image, format='png', bbox_inches="tight")
        output_image.seek(0)
        plt.close(fig)

        return output_image  # Retornar la imagen generada

    except Exception as e:
        self.update_state(state='FAILURE', meta={'error': str(e)})
        return {"error": str(e)}

# Ruta para iniciar la generación del sismograma
@app.route('/generate_sismograma', methods=['GET'])
def generate_sismograma():
    try:
        start_date_input = request.args.get("start")
        end_date_input = request.args.get("end")
        net = request.args.get("net")
        sta = request.args.get("sta")

        if not all([start_date_input, end_date_input, net, sta]):
            return jsonify({"error": "Faltan parámetros requeridos (start, end, net, sta)."}), 400

        # Iniciar tarea asincrónica en Celery
        task = generate_sismogram_task.apply_async(args=[start_date_input, end_date_input, net, sta])

        return jsonify({"task_id": task.id}), 202

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Ruta para verificar el estado de la tarea
@app.route('/task_status/<task_id>', methods=['GET'])
def task_status(task_id):
    task = generate_sismogram_task.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {'state': task.state, 'status': 'Esperando procesamiento'}
    elif task.state == 'SUCCESS':
        response = {'state': task.state, 'result': task.result}
    elif task.state == 'FAILURE':
        response = {'state': task.state, 'error': task.info.get('error', 'Error desconocido')}
    else:
        response = {'state': task.state}
    return jsonify(response)

# Ejecución en Render
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))  # Render usa el puerto 10000 por defecto
    app.run(host='0.0.0.0', port=port)
