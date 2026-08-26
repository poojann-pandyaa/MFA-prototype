import sys
sys.path.append('.')
from services.biometrics import extract_embedding
print("Running DeepFace...")
extract_embedding("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
print("DeepFace done.")
