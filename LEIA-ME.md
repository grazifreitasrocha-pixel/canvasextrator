# Sistema de Análise Fiscal — Versão Web

Backend (FastAPI) + Frontend (HTML/JS) + banco de dados. Suporta múltiplos
usuários, upload pela tela, cruzamento de NCM contra TIPI/benefícios
fiscais/substituição tributária, e cálculo do Simples Nacional (DAS) com
segregação de receita a partir do PGDAS-D do mês anterior.

## Estrutura

```
sistema-fiscal-web/
├── backend/
│   ├── app/
│   │   ├── main.py               — API (login, upload, processamento, download)
│   │   ├── parser_nfe.py         — extrai NCM/CEST/CFOP/valor dos XMLs
│   │   ├── parser_pgdas.py       — extrai RBT12 do PDF do PGDAS-D
│   │   ├── motor_analise.py      — cruza NCM contra TIPI/benefícios/ST
│   │   ├── simples_nacional.py   — calcula o DAS com segregação de receita
│   │   ├── gerar_planilha.py     — gera o Excel final (várias abas)
│   │   ├── classificador_ia.py   — validação de coerência NCM x descrição via IA
│   │   ├── bases/                — TIPI, benefícios ICMS/PIS-COFINS, ST (JSON)
│   │   └── frontend/
│   │       └── index.html        — o próprio backend serve essa tela
│   ├── requirements.txt
│   └── Dockerfile
└── .gitignore
```

## Rodando localmente (para testar antes do deploy)

```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**Importante:** o extrator do PGDAS-D usa o comando `pdftotext` (do
pacote `poppler-utils`), que não vem instalado por padrão no Mac/Windows.
Para testar essa função localmente:
- **Mac:** `brew install poppler`
- **Windows:** baixar poppler para Windows e adicionar ao PATH
- Sem isso instalado, o resto do sistema funciona normalmente — só a
  extração automática do RBT12 do PDF não vai funcionar localmente (mas
  funciona em produção, pois o Dockerfile já instala isso automaticamente)

Depois:
```bash
cd app
uvicorn main:app --reload
```

Acesse `http://localhost:8000` — o próprio backend já serve a tela.

## Deploy no Railway

### 1. Suba o código para o GitHub
```bash
cd sistema-fiscal-web
git init
git add .
git commit -m "versão completa"
git branch -M main
git remote add origin <url-do-seu-repositorio>
git push -u origin main --force
```

### 2. No Railway
1. **New Project** → **Deploy from GitHub repo** → seleciona o repositório
2. Em **Settings**, defina **Root Directory**: `backend`
3. O Railway detecta o `Dockerfile` automaticamente — não precisa configurar Start Command
4. **New** → **Database** → **PostgreSQL** (a `DATABASE_URL` é conectada automaticamente)
5. Em **Variables** do serviço backend, adicione:
   ```
   SECRET_KEY=<gere com: openssl rand -hex 32>
   ```
6. Deploy automático. O Railway gera uma URL pública (ex: `https://seu-projeto.up.railway.app`)

Acesse essa URL — já é a tela completa do sistema (login/cadastro).

## Como usar

1. Crie uma conta e faça login
2. Suba um `.zip` com XMLs de NF-e
3. **Opcional:** anexe o PDF do PGDAS-D do mês anterior (extrai o RBT12
   sozinho) ou digite o RBT12 manualmente, e escolha o Anexo (I-Comércio
   ou II-Indústria)
4. Baixe a planilha gerada, com as abas: **Resumo**, **ICMS**,
   **PIS-COFINS**, **Avisos** (divergências de CFOP em produtos com ST) e,
   se informado RBT12, **Simples Nacional** (DAS calculado e segregado)

## Bases de dados fiscais já incluídas

| Base | Conteúdo | Status |
|---|---|---|
| `tipi.json` | NCM × alíquota de IPI | ✅ Completa (TIPI oficial) |
| `beneficios_icms.json` | Isenção/redução/crédito outorgado (Anexo IX RCTE-GO) | ✅ 446 regras |
| `monofasico_piscofins.json` | Regime monofásico de PIS/COFINS | ✅ Tabelas SPED 4.3.10/4.3.11 |
| `substituicao_tributaria.json` | ICMS-ST por CEST/NCM | ✅ 517 regras (CONFAZ + Apêndice II RCTE-GO) |
| `beneficios_piscofins.json` | Benefícios específicos de PIS/COFINS por norma | ⚠️ Ainda vazio |

## Validação do cálculo do Simples Nacional

O motor de cálculo (`simples_nacional.py`) foi conferido contra um PGDAS-D
real: usando o RBT12 declarado, o sistema recalculou o DAS de cada faixa
de receita (normal, ST, monofásico, ST+monofásico) e bateu com o valor
oficial já apurado pela Receita Federal, com diferença de apenas R$ 0,01
(arredondamento). As tabelas de alíquotas (Anexo I e II) devem ser
conferidas periodicamente contra a tabela oficial vigente no Portal do
Simples Nacional, pois a legislação pode ser atualizada.

## Regras de negócio importantes

- **CFOP esperado para produtos com ST: 5405** (venda interna de
  mercadoria já tributada por substituição tributária). Notas com CFOP
  divergente aparecem na aba "Avisos". CFOP interestadual equivalente
  (6405) ainda não é validado.
- **Só entra na base do Simples Nacional a receita de venda** (CFOP
  começando com 5 ou 6) — notas de compra/entrada são ignoradas
  automaticamente no cálculo do DAS.

## O que ainda falta / limitações conhecidas

- Base de benefícios específicos de PIS/COFINS por norma ainda vazia
- Substituição tributária cobre só os Apêndices já processados do
  Anexo VIII (Apêndice II via PDF + planilha CONFAZ) — Apêndices I e X
  ainda não incluídos
- Processamento em background simples (sem fila dedicada) — adequado
  para o volume atual, mas pode precisar de Celery/RQ se o uso crescer
  muito
- CORS: configure `ALLOWED_ORIGINS` nas variáveis do Railway com o
  domínio real de produção antes de abrir para uso externo amplo
