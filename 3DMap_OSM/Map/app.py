from flask import Flask, send_from_directory

import os

app = Flask(__name__)

# Serve homepage → Three.js map
@app.route("/")
def index():
    return send_from_directory("static", "three.html")


# Serve GLB files placed in /models folder
@app.route("/models/<path:filename>")
def serve_model(filename):
    model_dir = os.path.join(app.root_path, "models")
    return send_from_directory(model_dir, filename)


# For any static files (JS, CSS)
@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
