# ===================================
# Ultra Suite — Dockerfile
# Node.js 20 LTS
# ===================================

FROM node:20-alpine

WORKDIR /app

# Installer les dépendances système
RUN apk add --no-cache python3 make g++

# Copier les fichiers de dépendances
COPY package.json package-lock.json* ./

# Installer les dépendances
RUN npm ci --only=production && npm cache clean --force

# Copier le code source
COPY . .

# Variables d'environnement par défaut
ENV NODE_ENV=production
ENV LOG_LEVEL=info

# Commande de démarrage
CMD ["node", "index.js"]