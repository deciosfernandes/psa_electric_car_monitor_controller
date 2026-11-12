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

> **Obrigatório antes de confiar na automação:** execute uma vez o script de diagnóstico
> `backend/scripts/spike_deferred.py` (ver [Script de diagnóstico](#script-de-diagnóstico-spike)). Ele confirma
> que o adiamento interrompe a carga e em que fuso horário o carro lê a hora programada; até lá a automação
> pode iniciar a carga à hora errada.

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
4. Em **Automação**, defina os preços EDA (o preset "EDA tri-horário semanal" pode ser editado) e confirme o
   desfasamento (10 min), o limite de bateria (80 %) e que a automação está ligada.
5. Execute o script de diagnóstico (abaixo) uma vez, antes de confiar na automação.

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
.venv/Scripts/python -m ruff check psacc tests
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

**Obrigatório**: execute **uma vez**, com o carro ligado à tomada e a carregar, antes de confiar na
automação. Confirma como a API trata o adiamento do carregamento, o significado de `charging.nextDelayedTime`
e se o carro lê a hora programada em hora local dos Açores ou em UTC (o carro é posto a não carregar e
reprogramado para ~3 min à frente; só conta um início de carga confirmado com dados frescos a essa hora; só é
possível no horário de inverno, quando os Açores estão em UTC-1). Demora entre 20 e 60 min (os pedidos de
"acordar" o carro estão limitados a 6 por 20 min).

```bash
cd backend
# Windows: .venv/Scripts/python | Linux/macOS: .venv/bin/python
.venv/Scripts/python scripts/spike_deferred.py
```

Escreve `spike-report.json` (sem tokens, VIN nem posição) na pasta de dados. O que verificar no relatório
está em `backend/psacc/psa/NOTES.md`. Veredicto do fuso horário: `local` — a automação pode ser usada;
`utc` — a automação não deve ser usada até a hora programada ser corrigida; `inconclusive` — o teste não
foi conclusivo (ver `reason`): não confie na automação e volte a correr o script (carro ligado à tomada,
bateria abaixo do limite, horário de inverno).

## Licença

GPL-3.0, ver `LICENSE`. Baseado no projeto psa_car_controller.
