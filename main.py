import os
import json
from fastapi import FastAPI
import paho.mqtt.client as mqtt
from supabase import create_client, Client

# Konfiguration aus Umgebungsvariablen (für Render.com)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")

# Supabase Client initialisieren
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

def on_connect(client, userdata, flags, rc):
    print(f"Verbunden mit HiveMQ (Result code {rc})")
    # Abonniere alle Geräte-Topics: balkon/[MAC-ADRESSE]/data
    client.subscribe("balkon/+/data")

def on_message(client, userdata, msg):
    try:
        # Extrahiere device_id aus dem Topic
        topic_parts = msg.topic.split('/')
        device_id = topic_parts[1]
        
        # Daten parsen
        payload = json.loads(msg.payload.decode())
        payload["device_id"] = device_id # Sicherstellen, dass die ID aus dem Topic kommt
        
        # In Supabase schreiben
        data = supabase.table("power_logs").insert(payload).execute()
        print(f"Daten für {device_id} gespeichert.")
    except Exception as e:
        print(f"Fehler beim Verarbeiten: {e}")

# MQTT Client Setup
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.tls_set() # Wichtig für HiveMQ Port 8883

@app.on_event("startup")
async def startup_event():
    mqtt_client.connect_async(MQTT_BROKER, 8883, 60)
    mqtt_client.loop_start()

@app.get("/")
def read_root():
    return {"status": "Bridge is running"}
