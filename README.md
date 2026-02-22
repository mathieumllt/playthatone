# __PlayThatOne__


---

## Stack

- **FastAPI** — back-end Python
- **SQLite** — base de données (fichier local)
- **WebSockets** — votes en temps réel
- **Vanilla HTML/JS** — front-end minimaliste

---

## Installation locale

```bash
# 1. Créer un environnement virtuel
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Configurer le token admin
cp .env .env.local
# Éditer .env.local et changer ADMIN_TOKEN

# 4. Lancer le serveur
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Ouvrir http://localhost:8000  
Panel admin : http://localhost:8000/admin-panel

---

## Déploiement Railway (recommandé)

```bash
# 1. Installer Railway CLI
npm install -g @railway/cli

# 2. Login
railway login

# 3. Créer le projet
railway init

# 4. Ajouter la variable d'environnement
railway variables set ADMIN_TOKEN=ton_mot_de_passe_secret

# 5. Déployer
railway up
```

Railway génère une URL HTTPS automatiquement → colle-la dans un QR code.

---

## Endpoints API

### Public
| Méthode | Route | Description |
|---------|-------|-------------|
| GET | `/songs` | Liste des chansons + votes |
| POST | `/vote/{id}` | Voter pour une chanson |
| WS | `/ws` | Flux temps réel |

### Admin (header: `Authorization: Bearer <token>`)
| Méthode | Route | Description |
|---------|-------|-------------|
| GET | `/admin/stats` | Stats + connectés |
| GET | `/admin/songs` | Catalogue complet |
| POST | `/admin/songs` | Ajouter une chanson |
| PATCH | `/admin/songs/{id}` | Modifier titre/artiste |
| DELETE | `/admin/songs/{id}` | Supprimer |
| POST | `/admin/songs/{id}/lyrics` | Ajouter/modifier paroles |
| POST | `/admin/reset` | Réinitialiser les votes |

---

## Structure du projet

```
playthatone/
├── main.py          # FastAPI — routes + WebSocket
├── database.py      # SQLite / SQLAlchemy
├── models.py        # Tables Song, Vote
├── schemas.py       # Pydantic schemas
├── requirements.txt
├── .env             # ADMIN_TOKEN
└── static/
    ├── index.html   # Front public (spectateurs)
    └── admin.html   # Panel admin
```
