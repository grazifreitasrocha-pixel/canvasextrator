-- ============================================================
-- SCHEMA: Sistema de Cruzamento NCM x TIPI x Benefícios Fiscais
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ------------------------------------------------------------
-- Usuários (multiusuário simples, sem multi-tenant/empresa)
-- ------------------------------------------------------------
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nome VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    senha_hash VARCHAR(255) NOT NULL,
    perfil VARCHAR(20) NOT NULL DEFAULT 'usuario', -- admin | usuario
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- Lotes de importação (cada upload de ZIP vira um lote)
-- ------------------------------------------------------------
CREATE TABLE lotes_importacao (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    usuario_id UUID REFERENCES users(id),
    nome_arquivo VARCHAR(500),
    total_xmls INTEGER DEFAULT 0,
    total_xmls_processados INTEGER DEFAULT 0,
    total_xmls_erro INTEGER DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'processando', -- processando | concluido | erro
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    concluido_em TIMESTAMPTZ
);

-- ------------------------------------------------------------
-- Notas fiscais (uma linha por NF-e dentro do ZIP)
-- ------------------------------------------------------------
CREATE TABLE notas_fiscais (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lote_id UUID REFERENCES lotes_importacao(id),
    chave_acesso VARCHAR(44),
    numero_nf VARCHAR(20),
    data_emissao DATE,
    cnpj_emitente VARCHAR(14),
    nome_emitente VARCHAR(255),
    uf_emitente CHAR(2),
    arquivo_origem VARCHAR(500),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_notas_chave ON notas_fiscais(chave_acesso);
CREATE INDEX idx_notas_lote ON notas_fiscais(lote_id);

-- ------------------------------------------------------------
-- Itens de nota fiscal (um produto por linha)
-- Esta é a tabela central pro cruzamento
-- ------------------------------------------------------------
CREATE TABLE itens_nota (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nota_id UUID REFERENCES notas_fiscais(id),
    numero_item INTEGER,
    codigo_produto VARCHAR(60),
    descricao_produto TEXT NOT NULL,
    ncm VARCHAR(8) NOT NULL,
    cfop VARCHAR(4),
    cst VARCHAR(4),
    ean VARCHAR(20),
    quantidade NUMERIC(15,4),
    unidade VARCHAR(10),
    valor_unitario NUMERIC(15,4),
    valor_total NUMERIC(15,2),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_itens_ncm ON itens_nota(ncm);
CREATE INDEX idx_itens_nota ON itens_nota(nota_id);

-- ------------------------------------------------------------
-- Tabela TIPI (referência oficial: NCM x alíquota IPI)
-- Fonte: Decreto 11.158/2022 + atualizações (ex: ADE RFB 001/2026)
-- ------------------------------------------------------------
CREATE TABLE tipi (
    ncm VARCHAR(8) PRIMARY KEY,
    descricao TEXT NOT NULL,
    aliquota_ipi VARCHAR(20), -- pode ser '0', '5', '10', 'NT' (não tributado), etc.
    capitulo VARCHAR(2),
    ex_tarifario VARCHAR(10),
    versao_fonte VARCHAR(100), -- ex: 'TIPI 2022 - Atualizada ADE 001-2026'
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- Normas de benefícios fiscais (uma linha por norma cadastrada)
-- Alimentada a partir da leitura dos PDFs
-- ------------------------------------------------------------
CREATE TABLE normas (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    titulo VARCHAR(500) NOT NULL,          -- ex: "Convênio ICMS 52/1991"
    esfera VARCHAR(20) NOT NULL,           -- federal | estadual | municipal
    tipo_norma VARCHAR(100),               -- lei, decreto, convenio, ato declaratorio...
    numero VARCHAR(50),
    data_publicacao DATE,
    uf VARCHAR(2),                         -- null se for federal
    tributo VARCHAR(30),                   -- ICMS | PIS/COFINS  (agrupamos PIS e COFINS pois seguem a mesma legislação)
    tipo_beneficio VARCHAR(50),            -- isencao, reducao_base, aliquota_zero, credito_presumido...
    vigencia_inicio DATE,
    vigencia_fim DATE,                     -- null = vigente por prazo indeterminado
    texto_resumo TEXT,                     -- resumo extraído do PDF
    arquivo_origem VARCHAR(500),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_tributo CHECK (tributo IN ('ICMS', 'PIS/COFINS'))
);

-- ------------------------------------------------------------
-- Relação NCM <-> Norma (uma norma pode cobrir vários NCMs,
-- e um NCM pode ter mais de um benefício aplicável)
-- ------------------------------------------------------------
CREATE TABLE beneficios_ncm (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    norma_id UUID REFERENCES normas(id),
    ncm VARCHAR(8) NOT NULL,               -- pode ser NCM completo (8 dig) ou prefixo (posição/capítulo)
    ncm_prefixo BOOLEAN DEFAULT FALSE,      -- true = aplica a todos NCMs que começam com este código
    condicoes TEXT,                        -- condições de aplicação (ex: "somente para uso hospitalar")
    observacoes TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_beneficios_ncm ON beneficios_ncm(ncm);

-- ------------------------------------------------------------
-- Regime de PIS/COFINS monofásico (Tabelas SPED 4.3.10/4.3.11)
-- Não é benefício de isenção — é regime de tributação concentrada,
-- mas precisa ficar visível pois muda a forma de apuração do tributo.
-- ------------------------------------------------------------
CREATE TABLE regime_monofasico_piscofins (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    codigo_sped VARCHAR(10),               -- código da tabela SPED (ex: '101', '201')
    ncm VARCHAR(8) NOT NULL,
    descricao_produto TEXT,
    aliquota_pis VARCHAR(20),
    aliquota_cofins VARCHAR(20),
    aliquota_zero BOOLEAN DEFAULT FALSE,   -- true = revenda com alíquota zero (etapa já tributada antes)
    vigencia_inicio DATE,
    vigencia_fim DATE,                     -- null = vigente
    origem_tabela VARCHAR(100),            -- 'Tabela 4.3.10' ou 'Tabela 4.3.11'
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_monofasico_ncm ON regime_monofasico_piscofins(ncm);

-- ------------------------------------------------------------
-- Substituição tributária de ICMS (Anexo VIII do RCTE ou equivalente)
-- Também não é benefício de isenção — é antecipação do imposto pelo
-- substituto tributário, mas precisa ficar visível porque muda
-- totalmente a apuração (CST, MVA, retenção antecipada).
-- ------------------------------------------------------------
CREATE TABLE substituicao_tributaria_icms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ncm VARCHAR(8) NOT NULL,
    ncm_prefixo BOOLEAN DEFAULT FALSE,
    descricao_produto TEXT,
    cest VARCHAR(10),                      -- Código Especificador da Substituição Tributária
    uf VARCHAR(2),                         -- UF a que se aplica (ST varia por estado/protocolo)
    mva_percentual NUMERIC(6,2),           -- Margem de Valor Agregado, quando aplicável
    norma_id UUID REFERENCES normas(id),
    observacoes TEXT,
    vigencia_inicio DATE,
    vigencia_fim DATE,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_st_ncm ON substituicao_tributaria_icms(ncm);
CREATE INDEX idx_st_cest ON substituicao_tributaria_icms(cest);

-- ------------------------------------------------------------
-- Resultado do cruzamento (o que vira a planilha final)
-- Gerado pelo motor de regras a cada processamento de lote
-- ------------------------------------------------------------
CREATE TABLE resultado_analise (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_nota_id UUID REFERENCES itens_nota(id),
    lote_id UUID REFERENCES lotes_importacao(id),
    descricao_produto TEXT,
    ncm VARCHAR(8),
    ncm_valido BOOLEAN,                    -- existe na TIPI vigente?
    tipi_descricao TEXT,
    tipi_aliquota_ipi VARCHAR(20),
    tem_beneficio_icms BOOLEAN NOT NULL DEFAULT FALSE,
    beneficios_icms JSONB,                 -- lista de {norma, tipo, condicoes}
    tem_beneficio_piscofins BOOLEAN NOT NULL DEFAULT FALSE,
    beneficios_piscofins JSONB,            -- lista de {norma, tipo, condicoes}
    regime_monofasico_piscofins BOOLEAN NOT NULL DEFAULT FALSE,
    detalhe_monofasico JSONB,              -- {codigo_sped, aliquota_pis, aliquota_cofins, aliquota_zero}
    sujeito_st_icms BOOLEAN NOT NULL DEFAULT FALSE,
    detalhe_st JSONB,                      -- {cest, uf, mva, norma}
    analise TEXT,                          -- texto gerado explicando o resultado
    alerta VARCHAR(255),                   -- ex: "NCM extinto - verificar substituto"
    processado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_resultado_lote ON resultado_analise(lote_id);
CREATE INDEX idx_resultado_ncm ON resultado_analise(ncm);
