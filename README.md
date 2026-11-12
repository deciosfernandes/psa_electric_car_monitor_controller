# psacc

Aplicação pessoal (um só utilizador) para controlar um Peugeot e-208 através da API ligada da PSA/Stellantis
e automatizar o carregamento de acordo com a tarifa EDA (tri-horário semanal, Açores).

- **Automação**: inicia o carregamento no início do vazio (por omissão 10 min depois, ex.: `00h10`) e adia-o
  antes do fim do vazio (ex.: `06h50`), respeitando o limite de bateria (por omissão 80 %).
- **Painel**: estado da bateria, autonomia, carregamento, preços EDA por período, registo de atividade.
- **Controlo**: "Carregar agora" (substitui a automação até ao limite de bateria ou até desligar o carro),
  pré-condicionamento, etc.
- **Alertas** opcionais por webhook do Discord (falha de comando, falha de autenticação PSA, MQTT em baixo).

Cargas, Viagens e Consumo mostram "Em breve".

> **Segurança:** a aplicação não deve ser exposta à Internet. Mantenha-a na rede local (LAN); tem apenas uma
> palavra-passe única e controla um veículo real.

## Instalação (Docker)

```bash
docker compose up -d --build
```

Abra `http://<servidor>:5000`.

### Primeira utilização

1. Defina a palavra-passe (primeiro acesso).
2. **Definições** → ligar a conta PSA (e-mail e palavra-passe da conta Peugeot).
3. Introduza o código recebido por SMS e o PIN da aplicação.
4. Em **Definições**, defina os preços EDA (o preset "EDA tri-horário semanal" pode ser editado).
5. Em **Automação**, confirme o desfasamento (10 min), o limite de bateria (80 %) e que a automação está ligada.

### Onde ficam os dados

Tudo em `./data` no anfitrião (montado em `/data` no contentor): base de dados, configuração e credenciais.
Faça cópias de segurança desta pasta. Fuso horário: `Atlantic/Azores`.

### Atualizar

```bash
git pull
docker compose up -d --build
```

Os dados em `./data` são preservados.

## Desenvolvimento

Backend (Python 3.12):

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -e '.[dev]'      # Linux/macOS: .venv/bin/pip
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check psacc --exclude psacc/psa/connected_car_api
PSACC_FAKE=1 .venv/Scripts/python -m uvicorn psacc.main:app --port 8000
```

`PSACC_FAKE=1` usa um carro falso em memória (modo demonstração, sem conta PSA). Nunca usar em produção.

Frontend (Node 22):

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173, /api faz proxy para http://127.0.0.1:8000
npm test
npm run lint
npm run build
```

### Script de diagnóstico (spike)

Execute **uma vez**, com o carro ligado à tomada e a carregar, para confirmar como a API trata o adiamento
do carregamento e o significado de `charging.nextDelayedTime`:

```bash
cd backend
# Windows: .venv/Scripts/python | Linux/macOS: .venv/bin/python
.venv/Scripts/python scripts/spike_deferred.py
```

Escreve `spike-report.json` (sem tokens, VIN nem posição) na pasta de dados.

## Licença

GPL-3.0, ver `LICENSE`. Baseado no projeto psa_car_controller.
