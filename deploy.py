import os

os.system("gcloud functions deploy hello_get --region southamerica-east1 --entry-point hello_get --runtime python37 --trigger-http --allow-unauthenticated")
os.system("gcloud functions deploy clean --region southamerica-east1 --entry-point clean --runtime python37 --trigger-http --allow-unauthenticated")