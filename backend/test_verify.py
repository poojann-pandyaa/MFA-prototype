from deepface import DeepFace
import numpy as np

img1_path = "https://raw.githubusercontent.com/serengil/deepface/master/tests/dataset/img1.jpg"
img2_path = "https://raw.githubusercontent.com/serengil/deepface/master/tests/dataset/img2.jpg"
img3_path = "https://raw.githubusercontent.com/serengil/deepface/master/tests/dataset/img3.jpg"

res = DeepFace.verify(img1_path=img1_path, img2_path=img2_path, model_name="ArcFace", distance_metric="cosine", enforce_detection=False)
res2 = DeepFace.verify(img1_path=img1_path, img2_path=img3_path, model_name="ArcFace", distance_metric="cosine", enforce_detection=False)
print("Same person distance:", res["distance"], "threshold:", res["threshold"])
print("Diff person distance:", res2["distance"], "threshold:", res2["threshold"])
