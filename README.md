# PSA Electric Car Monitor & Controller

Aplicação web pessoal para acompanhar e controlar um carro elétrico do grupo PSA/Stellantis (testada com um
Peugeot e-208) e **carregar automaticamente apenas nas horas de vazio** da tarifa da EDA — Electricidade dos
Açores (tri-horária, ciclo semanal).

> Baseado no projeto [**psa_car_controller**](https://github.com/flobz/psa_car_controller) de
> [**flobz**](https://github.com/flobz), autor original. Toda a integração com a PSA (login OAuth, código OTP,
> comandos remotos por MQTT) parte do trabalho de engenharia inversa desse projeto. Esta versão é uma reescrita
> para uso pessoal, com nova interface, nova arquitetura e automação por tarifa. Obrigado ao autor e a todos os
> contribuidores do projeto original.

## O que faz

- **Painel** — bateria, autonomia, estado de carga, faixa da tarifa de hoje (vazio / cheias / ponta) com o
  próximo evento automático, gráfico da bateria nas últimas 24 h e registo de atividade.
- **Automação de carga** — usa o modo de carga diferida do carro:
  - desliga o diferido no início do vazio (+ desfasamento, por omissão 10 min → `00h10`);
  - liga o diferido antes do fim do vazio (− desfasamento → `06h50`);
  - respeita um limite de bateria (por omissão 80 %);
  - junta janelas de vazio seguidas (ex.: sábado 23h00 → segunda 07h00) sem comandos à meia-noite;
  - confirma cada comando, volta a tentar e avisa se falhar.
- **Tarifa editável** — predefinição "EDA tri-horário semanal" (verão junho–outubro, inverno novembro–maio),
  grelha semanal em blocos de 15 min e preços por período com data de entrada em vigor.
- **Controlo** — "Carregar agora" (substitui a automação até ao limite ou até desligar o cabo), climatização,
  fechaduras, buzina, luzes e acordar o carro.
- **Alertas no Discord** — falha de comando, sessão PSA expirada, ligação MQTT em baixo; opcionalmente início
  e fim de carga.
- **Interface em português**, tema claro e escuro, horas no formato 24 h, fuso `Atlantic/Azores`.

Cargas, Viagens e Consumo aparecem como "Em breve".

## Avisos importantes

- **Use apenas na rede local (LAN).** A aplicação controla um carro real (incluindo destrancar) e é protegida
  só por uma palavra-passe. Não a exponha à Internet.
- **Execute o script de diagnóstico antes de confiar na automação** (ver abaixo). Só assim se confirma que o
  modo diferido interrompe a carga e em que fuso o carro lê a hora programada.
- A API da PSA não é oficial para este uso e pode mudar sem aviso.

## Instalação (Docker)

Requisitos: Docker com Docker Compose.

```bash
git clone https://github.com/deciosfernandes/psa_electric_car_monitor_controller.git
cd psa_electric_car_monitor_controller
docker compose up -d --build
```

Abra `http://<servidor>:5000`.

### Primeira utilização

1. Defina a palavra-passe da aplicação (primeiro acesso).
2. **Definições → Conta PSA**: escolha a aplicação (MyPeugeot, MyCitroën, …), o país e as credenciais da
   conta. Se o login automático falhar, a aplicação mostra um link para concluir o login manualmente e colar
   o código.
3. Peça o código por SMS e introduza-o juntamente com o PIN da aplicação móvel.
4. **Automação**: confirme o desfasamento (10 min), o limite de bateria (80 %), os horários da tarifa e
   preencha os preços de vazio, cheias e ponta.
5. **Definições → Discord** (opcional): cole o URL do webhook e use "Enviar teste".
6. Execute o script de diagnóstico (abaixo).

### Dados e cópias de segurança

Tudo fica em `./data` (montado em `/data` no contentor): base de dados, configuração, tokens da PSA, código
OTP e certificados. A pasta é criada e ajustada automaticamente com permissões só para a aplicação, que corre
sem privilégios de administrador. Faça cópias de segurança desta pasta e não a partilhe.

### Atualizar

```bash
git pull
docker compose up -d --build
```

Os dados em `./data` mantêm-se.

## Script de diagnóstico

Obrigatório **uma vez**, com o carro ligado à tomada e a bateria abaixo do limite, de preferência no horário
de inverno (quando os Açores estão em UTC−1, o teste do fuso é conclusivo). Demora 20–60 min, porque a PSA
limita os pedidos para acordar o carro.

**Pare a aplicação antes** (`docker compose down`): o script e a aplicação não podem usar as mesmas
credenciais da PSA ao mesmo tempo.

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -e '.[dev]'          # Linux/macOS: .venv/bin/pip
.venv/Scripts/python scripts/spike_deferred.py # Linux/macOS: .venv/bin/python
```

Para usar as mesmas credenciais do Docker, defina antes `PSACC_DATA_DIR` para a pasta `data` do repositório
(PowerShell: `$env:PSACC_DATA_DIR = "..\data"`).

O resultado fica em `spike-report.json` na pasta de dados, sem tokens, VIN nem localização. Veredicto do fuso:

| Resultado | Significado |
|---|---|
| `local` | O carro lê a hora em hora dos Açores — a automação pode ser usada. |
| `utc` | O carro lê a hora em UTC — não use a automação até isto ser corrigido. |
| `inconclusive` | O teste não foi conclusivo (ver `reason`) — volte a correr o script. |

Mais detalhes em [`backend/psacc/psa/NOTES.md`](backend/psacc/psa/NOTES.md).

## Desenvolvimento

Backend (Python 3.12, FastAPI):

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -e '.[dev]'
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check psacc tests
PSACC_FAKE=1 .venv/Scripts/python -m uvicorn psacc.main:app --port 8000
```

`PSACC_FAKE=1` usa um carro simulado em memória (modo de demonstração, com aviso visível na interface). Nunca
em produção.

Frontend (Node 22, Vite + React + TypeScript + Tailwind):

```bash
cd frontend
npm ci
npm run dev     # http://localhost:5173, /api encaminhado para http://127.0.0.1:8000
npm test
npm run lint
npm run build
```

A arquitetura e os comandos para quem desenvolve estão em [`CLAUDE.md`](CLAUDE.md); o desenho completo em
[`docs/superpowers/specs/`](docs/superpowers/specs/).

## Créditos e licença

- Projeto original: [flobz/psa_car_controller](https://github.com/flobz/psa_car_controller), por
  [flobz](https://github.com/flobz) e contribuidores.
- Horários da tarifa: folheto "Ciclo Semanal" da [EDA — Electricidade dos Açores](https://www.eda.pt).

Distribuído sob a licença **GPL-3.0**, tal como o projeto original — ver [`LICENSE`](LICENSE).
