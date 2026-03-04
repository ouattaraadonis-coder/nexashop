from flask import Flask, jsonify
import os

app = Flask(__name__)

@app.route("/")
def index():
    return "NexaShop fonctionne !"

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
```

Committez, attendez le redémarrage Railway, puis testez :
```
https://nexashop-production.up.railway.app
→ doit afficher : NexaShop fonctionne !
