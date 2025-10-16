import streamlit as st
import pandas as pd
import requests
from requests.exceptions import RequestException
from st_aggrid import AgGrid, GridOptionsBuilder
from requests.auth import HTTPBasicAuth
import base64
import json
from pathlib import Path
from datetime import date
import io
import pdfplumber
import pyodbc
from datetime import datetime, timedelta

#BASE DE FATURAMENTOS/CLIENTES    
def obter_faturamento_sql():
    try:
        #lê excel
        df_construtoras = pd.read_excel(
            "Integrador Ambar.xlsx",
            sheet_name="LISTA DE CONSTRUTORAS",
            usecols=["EMPRESA", "CÓDIGO"]
        )
        df_construtoras.rename(columns={"EMPRESA": "Construtora", "CÓDIGO": "Telex"}, inplace=True)
        df_construtoras["Telex"] = df_construtoras["Telex"].astype(str).str.strip()

        #conexão sql server
        conn = pyodbc.connect(
            "DRIVER={ODBC Driver 17 for SQL Server};"
            "SERVER=45.6.154.46,1819;"
            "DATABASE=CVNIMG_134415_PR_PD;"
            "UID=CLT134415READ;"
            "PWD=edwna17483AYGMJ@!;"
        )

        data_12_meses_atras = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d')
        cf_lista = [
            5101,5102,5116,5120,5122,5123,5124,5125,5401,5403,5933,
            6101,6102,6107,6108,6116,6120,6122,6124,6125,6401,6403,
            6501,6502,6933,7101,7102,6109,6123,5405
        ]
        placeholders = ",".join("?" * len(cf_lista))

        #JOIN da SC6G10 (faturamento) com SA1G10 (clientes)
        query = f"""
        SELECT 
            C6.C6_PRODUTO AS [Código],
            C6.C6_QTDVEN AS [Quantidade],
            C6.C6_VALOR AS [Valor],
            C6.C6_CLI AS [Código Cliente],
            C6.C6_LOJA AS [Loja Faturamento],
            C6.C6_DATFAT AS [Data Faturamento],
            C6.C6_CF AS [CF],
            A1.A1_NOME AS [Nome Cliente],
            A1.A1_CGC AS [CNPJ/CPF],
            A1.A1_TELEX AS [Telex],
            A1.A1_LOJA AS [Loja Cliente]
        FROM SC6G10 C6
        LEFT JOIN SA1G10 A1 
            ON A1.A1_COD = C6.C6_CLI 
           AND A1.A1_LOJA = C6.C6_LOJA
        WHERE C6.D_E_L_E_T_ <> '*' 
          AND C6.C6_BLQ <> 'R' 
          AND C6.C6_DATFAT >= ? 
          AND C6.C6_CF IN ({placeholders})
        """

        df_faturamento = pd.read_sql(query, conn, params=[data_12_meses_atras] + cf_lista)
        conn.close()

        # Ajustes de formato
        df_faturamento["Código"] = df_faturamento["Código"].astype(str).str.zfill(6)
        df_faturamento["Data Faturamento"] = pd.to_datetime(df_faturamento["Data Faturamento"], errors="coerce")
        df_faturamento["Telex"] = df_faturamento["Telex"].astype(str).str.strip()
        df_final = df_faturamento.merge(df_construtoras, on="Telex", how="left")

        colunas = ["Construtora", "Nome Cliente", "Código Cliente", "Loja Cliente", "CNPJ/CPF", "Data Faturamento", "Telex", "CF",
            "Código", "Quantidade", "Valor"]
        
        df_final = df_final[[c for c in colunas if c in df_final.columns]]

        #Classificação por faturamento
        df_classificacao = (
            df_final.groupby("Construtora", as_index=False)["Valor"]
            .sum()
            .rename(columns={"Valor": "Faturamento Total"})
        )

        def classificar_tamanho(fat):
            if fat < 300000:
                return "P"
            elif 300000 <= fat < 500000:
                return "M"
            elif 500000 <= fat < 900000:
                return "G"
            else:
                return "GG"

        df_classificacao["Tamanho"] = df_classificacao["Faturamento Total"].apply(classificar_tamanho)
        df_classificacao["Faturamento Total"] = df_classificacao["Faturamento Total"].apply(
            lambda x: f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )

        ordem_tamanhos = ["GG", "G", "M", "P"]
        df_classificacao["Tamanho"] = pd.Categorical(df_classificacao["Tamanho"], categories=ordem_tamanhos, ordered=True)
        df_classificacao = df_classificacao.sort_values(by="Tamanho", ascending=True)

        # junta novamente com df_final (caso queira ver junto dos registros)
        df_final = df_final.merge(df_classificacao, on="Construtora", how="left")
        df_final["Preço Unitário"] = df_final["Valor"] / df_final["Quantidade"]

        df_media_preco = (
            df_final.groupby(["Código", "Tamanho"], as_index=False)["Preço Unitário"]
            .mean()
            .rename(columns={"Preço Unitário": "Preço Médio"})
        )

        # formata em reais
        df_media_preco["Preço Médio"] = df_media_preco["Preço Médio"].apply(
            lambda x: f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        # ordena pelo tamanho (GG, G, M, P)
        ordem_tamanhos = ["GG", "G", "M", "P"]
        df_media_preco["Tamanho"] = pd.Categorical(df_media_preco["Tamanho"], categories=ordem_tamanhos, ordered=True)
        df_media_preco = df_media_preco.sort_values(["Código", "Tamanho"])

        return df_final, df_classificacao, df_media_preco

    except Exception as e:
        st.error(f"Erro ao obter faturamento via SQL Server: {e}")
        print(f"Erro ao obter faturamento via SQL Server: {e}")
        return pd.DataFrame()


st.sidebar.header("📑 Base de Faturamento")
#controla a exibição
if "mostrar_faturamento" not in st.session_state:
    st.session_state.mostrar_faturamento = False
if "mostrar_classificacao" not in st.session_state:
    st.session_state.mostrar_classificacao = False
if "mostrar_media_preco" not in st.session_state:
    st.session_state.mostrar_media_preco = False

# Botões na barra lateral
if st.sidebar.button("📊 Faturamento Detalhado"):
    st.session_state.mostrar_faturamento = not st.session_state.mostrar_faturamento
if st.sidebar.button("🏗️ Categorização de Clientes"):
    st.session_state.mostrar_classificacao = not st.session_state.mostrar_classificacao
if st.sidebar.button("💰 Média de Preços por Tamanho"):
    st.session_state.mostrar_media_preco = not st.session_state.mostrar_media_preco

if "dados_carregados" not in st.session_state:
    st.session_state.df_faturamento, st.session_state.df_classificacao, st.session_state.df_media_preco = obter_faturamento_sql()
    st.session_state.dados_carregados = True

if st.session_state.mostrar_faturamento:
    df_faturamento = st.session_state.df_faturamento
    if not df_faturamento.empty:
        total_registros = len(df_faturamento)
        data_min = df_faturamento["Data Faturamento"].min()
        data_max = df_faturamento["Data Faturamento"].max()
        st.subheader("📊 Faturamento (Últimos 12 Meses)")
        st.write(f"**Total de registros:** {total_registros:,}")
        st.write(f"**Período:** {data_min.date()} ➜ {data_max.date()}")
        st.dataframe(df_faturamento, hide_index=True)
    else:
        st.warning("⚠️ Nenhum dado de faturamento foi retornado.")

if st.session_state.mostrar_classificacao:
    df_classificacao = st.session_state.df_classificacao
    st.subheader("🏗️ Categorização de Clientes por Faturamento")
    st.dataframe(df_classificacao, hide_index=True)

if st.session_state.mostrar_media_preco:
    df_media_preco = st.session_state.df_media_preco
    st.subheader("💰 Média de Preço por Produto e Tamanho de Cliente")
    st.dataframe(df_media_preco, hide_index=True)


# config inicial da pág
st.set_page_config(page_title="Calculadora de Precificação", layout="wide")
st.title("Precificação")
st.markdown("### Parâmetros")

col1, col2 = st.columns(2)
with col1:
    tipo_operacao = st.selectbox("Tipo de operação", ["Preço final fixo", "Margem fixa"])
with col2:
    segmento = st.selectbox("Cliente", ["Construtora", "Canais"])

if "segmento_anterior" not in st.session_state:
    st.session_state.segmento_anterior = segmento
# Verifica se o segmento mudou
if st.session_state.segmento_anterior != segmento:
    st.session_state.segmento_anterior = segmento
    st.session_state.df_editado["ICMS ST(%)"] = 0
    st.rerun()

col3, col4 = st.columns(2)
with col3:
    estados = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PI", "PB", "PE",
               "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]
    estado = st.selectbox("Estado", estados)
with col4:
    condicoes_pagamento = ["2X(45,60 DIAS)", "TODO DIA 15 FORA DO MÊS", "À VISTA", "1X(15 DIAS)", "1X(21 DIAS)",
                           "1X(28 DIAS)", "1X(30 DIAS)", "1X(45 DIAS)", "1X(60 DIAS)", "1X(120 DIAS) - LEROY",
                           "2X(21,42 DIAS)", "2X(30,60 DIAS)", "3X(21,42,63 DIAS)", "50% ANTECIPADO, 50% 10 DDL",
                           "HM (1,45 DD, QUARTA-FEIRA)", "CAP - 3X(45,56,82)", "MRV - 60 DD, DIA 10 E 25", "CYR E CURY",
                           "ESP CLIMA - 2X(21/42 DIAS)", "ESP CLIMA - 1X(45 DIAS)", "STA ANGELA - 1X(28 DD, DIA 15)",
                           "PROMOVAL - 1X(30 DD, 15 E 30)", "FRIOVIX E PORTO (90 DD)", "JCM (28,35,42,56)",
                           "YOSHI E YTICON", "DUE E ACLF 2X(45/60)", "1X(180 DIAS)", "3X(10,15,21)", "COOPERCON(45/60)",
                           "BRZ (30,60,90)", "3X(30,60,90)"]
    cond_pagamento = st.selectbox("Condição de pagamento", condicoes_pagamento)

#puxa planilha estados
@st.cache_data
def carregar_icms_estados():
    df_icms = pd.read_excel("estado destino.xlsx", sheet_name="Planilha1", skiprows=1, header=None)  # lê a planilha
    df_icms.columns = ['Estado', 'ICMS_Destino', 'ICMS_SP_Destino', 'DIFAL', 'Frete']  # define os nomes das colunas
    return df_icms
df_icms = carregar_icms_estados()

#puxa planilha condpag
@st.cache_data
def carregar_condicoes_pagamento():
    df_condpag = pd.read_excel("condpag.xlsx", sheet_name="Planilha1", skiprows=1, header=None)
    df_condpag.columns = ['Condicao', 'Juros']
    return df_condpag

# criar layout com 3 colunas
col_frete, col_legendas1, col_legendas2 = st.columns(3)
with col_frete:
    frete_incluso = st.checkbox("Frete incluso no preço?", value=False)

# procura no dataframe df_icms a linha onde a coluna 'Estado' é igual ao estado escolhido
linha_estado = df_icms[df_icms['Estado'] == estado]
if not linha_estado.empty:
    icms_destino = linha_estado['ICMS_Destino'].values[0]
    icms_sp_destino = linha_estado['ICMS_SP_Destino'].values[0]
    #define DIFAL
    if segmento == "Canais":
        difal = 0
    else:
        difal = linha_estado['DIFAL'].values[0]
    #impostos fixos
    pis = 1.65
    cofins = 7.6
    # calculo frete
    valor_frete = float(linha_estado['Frete'].values[0]) if frete_incluso else 0.0
    # calculo juros
    df_condpag = carregar_condicoes_pagamento()
    juros = 0

    if not df_condpag.empty:
        # Normaliza o texto para evitar diferenças de maiúsculas, espaços e acentos
        condicao_normalizada = cond_pagamento.strip().lower()
        df_condpag['Condicao_norm'] = df_condpag['Condicao'].str.strip().str.lower()
        linha_juros = df_condpag[df_condpag['Condicao_norm'] == condicao_normalizada]
        if not linha_juros.empty:
            juros = linha_juros['Juros'].values[0]
        else:
            juros = 0
        if not linha_juros.empty:
            juros = linha_juros['Juros'].values[0]

    with col_legendas1:
        st.markdown(f"""
                    <style>
                        .tiny-font {{
                            font-size: 0.9em !important;
                            color: white !important;
                            line-height: 1.2;
                        }}
                    </style>
                    <div class="tiny-font">
                        • ICMS Destino: {icms_destino * 100}%<br>
                        • ICMS SP X Dest: {icms_sp_destino * 100:.1f}%<br>
                        • DIFAL: {difal * 100}%
                    </div>
                    """, unsafe_allow_html=True)
    with col_legendas2:
        st.markdown(f"""
                    <style>
                        .tiny-font {{
                            font-size: 0.9em !important;
                            color: white !important;
                            line-height: 1.2;
                        }}
                    </style>
                    <div class="tiny-font">
                        • PIS: {pis}%<br>
                        • COFINS: {cofins}%<br>
                        • Juros: {juros}%<br>
                        • Frete: {valor_frete * 100}%
                    </div>
                    """, unsafe_allow_html=True)
else:
    st.warning("Estado selecionado não encontrado na tabela de ICMS.")

modo_entrada = st.radio("Como deseja inserir os produtos?", ["Digitar manualmente", "Upload de planilha", "Ler PDF"])
df_upload = None  # inicializa vazio
if modo_entrada == "Upload de planilha":
    uploaded_file = st.file_uploader("Faça upload da planilha de produtos", type=["xlsx"])
    if uploaded_file is not None:
        try:
            df_upload = pd.read_excel(uploaded_file)
            df_upload.columns = df_upload.columns.str.strip()
            colunas_esperadas = ["Código"]
            if tipo_operacao == "Margem fixa":
                colunas_esperadas.append("Margem Bruta")
            else:
                colunas_esperadas.append("Preço s/ IPI")
            faltando = [c for c in colunas_esperadas if c not in df_upload.columns]
            if faltando:
                st.error(f"A planilha precisa conter as colunas: {faltando}")
                df_upload = None
            else:
                st.success("Planilha carregada com sucesso.")
        except Exception as e:
            st.error(f"Erro ao ler a planilha: {e}")
            df_upload = None

elif modo_entrada == "Ler PDF":
    uploaded_pdf = st.file_uploader("Faça upload do PDF de produtos", type=["pdf"])
    if uploaded_pdf is not None:
        try:
            data = []
            with pdfplumber.open(uploaded_pdf) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if table:
                        # transforma em DataFrame
                        df_temp = pd.DataFrame(table[1:], columns=table[0])
                        # procura as colunas necessárias
                        colunas_disponiveis = [c.strip().upper() for c in df_temp.columns]
                        if "CÓDIGO" in colunas_disponiveis and "VLR. UNIT" in colunas_disponiveis:
                            df_filtrado = df_temp[["CÓDIGO", "VLR. UNIT"]].copy()
                            df_filtrado.rename(columns={"CÓDIGO": "Código", "VLR. UNIT": "Preço s/ IPI"}, inplace=True)
                            data.append(df_filtrado)

            if data:
                df_upload = pd.concat(data, ignore_index=True)
                # normaliza os dados
                df_upload["Código"] = df_upload["Código"].astype(str).str.zfill(6)
                df_upload["Preço s/ IPI"] = (
                    df_upload["Preço s/ IPI"].astype(str)
                    .str.replace(",", ".", regex=False)
                    .str.replace("R$", "", regex=False)
                    .str.strip()
                )
                df_upload["Preço s/ IPI"] = pd.to_numeric(df_upload["Preço s/ IPI"], errors="coerce")
                st.success("PDF carregado e processado com sucesso.")
            else:
                st.error("Não encontrei as colunas 'CÓDIGO' e 'VLR. UNIT' no PDF.")
                df_upload = None

        except Exception as e:
            st.error(f"Erro ao ler o PDF: {e}")
            df_upload = None

@st.cache_data
def carregar_base_cpv():
    # Caminho do novo arquivo salvo localmente
    caminho_arquivo = r"skus ativos.xlsx"

    # Ler a aba (caso a planilha tenha várias, especifique o nome certo)
    df_cpv = pd.read_excel(caminho_arquivo, sheet_name="SKUs ativos")

    # Ajuste os nomes das colunas conforme a planilha
    df_cpv = df_cpv.rename(columns={
        "Cód Produto": "Código",
        "Descrição": "Descrição",
        "CPV": "CPV",
        "Data última alteração": "Data última alteração"
    })

    # Garante que o código fique sempre com 6 dígitos
    df_cpv["Código"] = df_cpv["Código"].astype(str).str.zfill(6)

    # Converte CPV para número, mesmo se vier com vírgula
    df_cpv["CPV"] = (
        df_cpv["CPV"]
        .astype(str)
        .str.replace(',', '.', regex=False)
        .astype(float)
    )

    return df_cpv
df_base_cpv = carregar_base_cpv()
df_cpv_filtrado = df_base_cpv[["Código", "Descrição", "CPV"]]

# obter IPI da api
def obter_ipi_ncm_api():
    try:
        url = "http://ambartech134415.protheus.cloudtotvs.com.br:1807/rest/api/v1/pricingcomponentes2022/sb1"
        response = requests.get(url, auth=HTTPBasicAuth("ambar.integracao", "!ambar@2025int"))
        response.raise_for_status()
        dados_api = response.json()
        df_ipi = pd.DataFrame(dados_api)
        df_ipi["B1_COD"] = df_ipi["B1_COD"].astype(str).str.zfill(6)
        # converte para valor numerico
        df_ipi["B1_IPI"] = pd.to_numeric(df_ipi["B1_IPI"], errors='coerce')
        df_ipi["B1_IPI"] = df_ipi["B1_IPI"].fillna(0)
        df_ipi = df_ipi[["B1_COD", "B1_IPI", "B1_POSIPI"]]
        df_ipi.rename(columns={"B1_COD": "Código", "B1_IPI": "IPI", "B1_POSIPI": "NCM"}, inplace=True)
        return df_ipi
    except RequestException as e:
        st.error(f"Erro ao obter dados da API: {e}")
        return pd.DataFrame(columns=["Código", "IPI", "NCM"])
df_ipi_ncm = obter_ipi_ncm_api()

@st.cache_data
def carregar_icms_st():
    df_st = pd.read_excel("icms st(%) - completo.xlsx")
    # Ajusta os dados
    df_st.columns = df_st.columns.str.strip()
    # Normaliza NCM: 8 dígitos, sem espaços e sem pontos
    df_st["NCM"] = (df_st["NCM"].astype(str).str.replace(r'\D', '', regex=True).str.zfill(8))
    df_st["ESTADO"] = df_st["ESTADO"].astype(str).str.strip()
    return df_st
df_icms_st = carregar_icms_st()
if not df_ipi_ncm.empty:
    # merge para incluir IPI e NCM
    df_cpv_ipi = pd.merge(df_cpv_filtrado, df_ipi_ncm, on="Código", how="left")

    def buscar_icms_st(row, estado_selecionado, segmento_selecionado):
        try:
            if segmento_selecionado == "Construtora":
                return 0
            ncm_produto = (str(row["NCM"]).replace('.', '').replace('-', '').strip().zfill(8)) if pd.notna(
                row["NCM"]) else ""
            estado_busca = estado_selecionado.strip().upper()
            df_filtrado = df_icms_st[(df_icms_st["NCM"] == ncm_produto) & (df_icms_st["ESTADO"] == estado_busca)]
            if not df_filtrado.empty:
                return df_filtrado.iloc[0]["Alíquota Efetiva"]
            return 0
        except Exception as e:
            st.error(f"Erro ao buscar ICMS ST: {e}")
            return 0

    if not df_ipi_ncm.empty:
        df_cpv_ipi = pd.merge(df_cpv_filtrado, df_ipi_ncm, on="Código", how="left")
        # Recalcular ICMS ST(%) sempre que o estado mudar ou for a primeira vez
        if "df_editado" not in st.session_state or st.session_state.get("estado_atual") != estado:
            df_cpv_ipi["ICMS ST(%)"] = df_cpv_ipi.apply(lambda row: buscar_icms_st(row, estado, segmento), axis=1)
            st.session_state.df_editado = df_cpv_ipi.copy()
            st.session_state.estado_atual = estado
        else:
            df_cpv_ipi = st.session_state.df_editado.copy()
    if df_upload is not None and "df_editado" in st.session_state:
        # Garantir que o código seja string com 6 dígitos
        df_upload["Código"] = df_upload["Código"].astype(str).str.zfill(6)
        # Criar uma coluna para marcar os produtos do upload
        st.session_state.df_editado["Do_Upload"] = False
        for _, row_upload in df_upload.iterrows():
            codigo = row_upload["Código"]
            mask = st.session_state.df_editado["Código"] == codigo
            if mask.any():
                idx = st.session_state.df_editado.index[mask].tolist()[0]
                # Marcar como produto do upload
                st.session_state.df_editado.at[idx, "Do_Upload"] = True
                # Preencher os valores conforme o tipo de operação
                if tipo_operacao == "Margem fixa" and "Margem Bruta" in row_upload:
                    st.session_state.df_editado.at[idx, "Margem Bruta"] = row_upload["Margem Bruta"]
                elif tipo_operacao == "Preço final fixo" and "Preço s/ IPI" in row_upload:
                    st.session_state.df_editado.at[idx, "Preço s/ IPI"] = row_upload["Preço s/ IPI"]
    if tipo_operacao == "Margem fixa":
        # garantir colunas iniciais e valores padrão
        if "df_editado" not in st.session_state:
            df_cpv_ipi["Margem Bruta"] = ""
            df_cpv_ipi["Base de cálculo"] = None
            df_cpv_ipi["Lucro Bruto"] = None
            df_cpv_ipi["Frete"] = None
            # Criar colunas de impostos sem sobrescrever ICMS ST(%)
            if "ICMS" not in df_cpv_ipi.columns:
                df_cpv_ipi["ICMS"] = icms_destino
            else:
                df_cpv_ipi["ICMS"].fillna(icms_destino, inplace=True)
            if "DIFAL" not in df_cpv_ipi.columns:
                df_cpv_ipi["DIFAL"] = difal
            else:
                df_cpv_ipi["DIFAL"].fillna(difal, inplace=True)
            if "PIS" not in df_cpv_ipi.columns:
                df_cpv_ipi["PIS"] = pis
            else:
                df_cpv_ipi["PIS"].fillna(pis, inplace=True)
            if "COFINS" not in df_cpv_ipi.columns:
                df_cpv_ipi["COFINS"] = cofins
            else:
                df_cpv_ipi["COFINS"].fillna(cofins, inplace=True)
            st.session_state.df_editado = df_cpv_ipi.copy()
        else:
            if frete_incluso:
                st.session_state.df_editado["Frete"] = st.session_state.df_editado["Preço s/ IPI"].fillna(
                    0) * valor_frete
            else:
                st.session_state.df_editado["Frete"] = 0

    def calcular_base_de_calculo():
        df_editado = pd.DataFrame(grid_response["data"])
        st.session_state.df_editado = df_editado.copy()
        def calcular_linha(row):
            try:
                taxa_icms = icms_sp_destino
                taxa_pis = pis / 100
                taxa_difal = difal
                taxa_cofins = cofins / 100
                taxa_juros = juros / 100
                taxa_frete = float(valor_frete) if frete_incluso else 0.0
                ipi_percent = float(row["IPI"]) / 100 if pd.notna(row["IPI"]) else 0
                try:
                    coef = float(str(row.get("Coeficiente", "0.2")).replace(",", "."))
                except:
                    coef = 0.2

                if tipo_operacao == "Margem fixa":
                    editavel = "Margem Bruta"
                    margem_str = str(row.get("Margem Bruta", "")).replace(",", ".").replace("%", "").strip()

                    # Se vazio ou inválido → não calcular
                    if margem_str == "" or margem_str.lower() == "nan":
                        return pd.Series([None] * 14)
                    try:
                        margem_decimal = float(margem_str) / 100
                    except ValueError:
                        return pd.Series([None] * 14)
                    if 0 < margem_decimal < 1:
                        base_calculo = row["CPV"] / (1 - margem_decimal)
                        lucro_bruto = margem_decimal * base_calculo

                        if frete_incluso:
                            sumtax = taxa_icms + taxa_pis + taxa_difal + taxa_cofins
                            #coef = 0
                            frete_valor = (base_calculo * (1 + sumtax)) / (
                                        1 / (taxa_frete * (1 + coef) * (1 + taxa_juros)) - sumtax)
                            icms_TAB = (base_calculo + frete_valor) * taxa_icms
                            difal_TAB = (base_calculo + frete_valor) * taxa_difal
                            pis_TAB = (base_calculo + frete_valor) * taxa_pis
                            cofins_TAB = (base_calculo + frete_valor) * taxa_cofins
                            if segmento == "Canais":
                                icms_st_porcentagem = row["ICMS ST(%)"]
                                icms_st_TAB = (base_calculo + frete_valor) * icms_st_porcentagem
                            else:
                                icms_st_TAB = 0
                            preco_sem_ipi = frete_valor / taxa_frete
                            taxa_ipi = preco_sem_ipi * ipi_percent
                            preco_com_ipi = preco_sem_ipi + taxa_ipi
                            preco_final = preco_com_ipi + frete_valor
                            preco_totvs = (preco_final) / ((1 + taxa_juros) * (1 + ipi_percent))

                        else:
                            frete_valor = 0
                            #coef = 0
                            icms_TAB = base_calculo * taxa_icms
                            difal_TAB = base_calculo * taxa_difal
                            pis_TAB = base_calculo * taxa_pis
                            cofins_TAB = base_calculo * taxa_cofins
                            if segmento == "Canais":
                                icms_st_porcentagem = row["ICMS ST(%)"]
                                icms_st_TAB = base_calculo * icms_st_porcentagem
                            else:
                                icms_st_TAB = 0
                            IMP = icms_TAB + difal_TAB + pis_TAB + cofins_TAB + icms_st_TAB
                            preco_sem_ipi = ((base_calculo + IMP) * (1 + taxa_juros)) * (1 + coef)
                            taxa_ipi = preco_sem_ipi * ipi_percent
                            preco_com_ipi = preco_sem_ipi + taxa_ipi
                            preco_final = preco_com_ipi
                            preco_totvs = (preco_final) / ((1 + taxa_juros) * (1 + ipi_percent))
                        return pd.Series([round(base_calculo, 2), round(lucro_bruto, 2), round(frete_valor, 2),
                                          round(preco_sem_ipi, 2), round(icms_TAB, 2), round(pis_TAB, 2),
                                          round(difal_TAB, 2), round(cofins_TAB, 2), round(taxa_ipi, 3),
                                          round(preco_com_ipi, 2), round(preco_totvs, 2), round(icms_st_TAB, 2), coef,
                                          round(preco_final, 2)])
                    return pd.Series([None] * 14)
                elif tipo_operacao == "Preço final fixo":
                    preco_str = str(row.get("Preço s/ IPI", "0")).strip()
                    preco_str = preco_str.replace('%', '').replace('R$', '').replace(' ', '')
                    preco_str = preco_str.replace(',', '.')
                    try:
                        preco_sem_ipi = float(preco_str)
                    except ValueError:
                        preco_sem_ipi = 0
                    editavel = "Preço s/ IPI"
                    if frete_incluso:
                        frete_valor = preco_sem_ipi * taxa_frete
                        #coef = 0.2
                        sumtax = taxa_icms + taxa_pis + taxa_difal + taxa_cofins
                        base_calculo = (((preco_sem_ipi / (1 + coef)) / (1 + taxa_juros)) - (frete_valor * sumtax)) / (
                                    1 + sumtax)
                        lucro_bruto = base_calculo - row["CPV"]
                        icms_TAB = (base_calculo + frete_valor) * taxa_icms
                        difal_TAB = (base_calculo + frete_valor) * taxa_difal
                        pis_TAB = (base_calculo + frete_valor) * taxa_pis
                        cofins_TAB = (base_calculo + frete_valor) * taxa_cofins
                        if segmento == "Canais":
                            icms_st_porcentagem = row["ICMS ST(%)"]
                            icms_st_TAB = (base_calculo + frete_valor) * icms_st_porcentagem
                        else:
                            icms_st_TAB = 0
                        margem = (lucro_bruto / base_calculo) * 100
                        taxa_ipi = preco_sem_ipi * ipi_percent
                        preco_com_ipi = preco_sem_ipi + taxa_ipi
                        preco_final = preco_com_ipi + frete_valor
                        preco_totvs = (preco_final) / ((1 + taxa_juros) * (1 + ipi_percent))
                    else:
                        frete_valor = 0
                        #coef = 0.2
                        sumtax = taxa_icms + taxa_pis + taxa_difal + taxa_cofins
                        base_calculo = ((preco_sem_ipi / (1 + coef)) / (1 + taxa_juros)) / (1 + sumtax)
                        lucro_bruto = base_calculo - row["CPV"]
                        icms_TAB = base_calculo * taxa_icms
                        difal_TAB = base_calculo * taxa_difal
                        pis_TAB = base_calculo * taxa_pis
                        cofins_TAB = base_calculo * taxa_cofins
                        if segmento == "Canais":
                            icms_st_porcentagem = row["ICMS ST(%)"]
                            icms_st_TAB = base_calculo * icms_st_porcentagem
                        else:
                            icms_st_TAB = 0
                        margem = (lucro_bruto / base_calculo) * 100
                        taxa_ipi = preco_sem_ipi * ipi_percent
                        preco_com_ipi = preco_sem_ipi + taxa_ipi
                        preco_final = preco_com_ipi
                        preco_totvs = (preco_final) / ((1 + taxa_juros) * (1 + ipi_percent))
                return pd.Series([round(base_calculo, 2), round(icms_TAB, 2), round(difal_TAB, 3), round(pis_TAB, 2),
                                  round(cofins_TAB, 2), round(icms_st_TAB, 2), round(margem, 3), round(taxa_ipi, 2),
                                  round(preco_com_ipi, 2), round(preco_totvs, 2), coef, round(lucro_bruto, 3),
                                  round(frete_valor, 2), round(preco_final, 2)])
            except Exception as e:
                st.error(f"Erro no cálculo: {e}")
            if tipo_operacao == "Margem fixa":
                return pd.Series([None] * 14)
            else:
                return pd.Series([None] * 14)
        if tipo_operacao == "Margem fixa":
            st.session_state.df_editado[[
                "Base de cálculo", "Lucro Bruto", "Frete",
                "Preço s/ IPI", "ICMS", "PIS", "DIFAL", "COFINS",
                "TAXA IPI", "Preço c/ IPI", "Preço TOTVS", "ICMS ST", "Coeficiente", "Preço Final c/ Frete"
            ]] = st.session_state.df_editado.apply(calcular_linha, axis=1)
        elif tipo_operacao == "Preço final fixo":
            resultados = st.session_state.df_editado.apply(calcular_linha, axis=1)
            resultados.columns = [
                "Base de cálculo", "ICMS", "DIFAL", "PIS", "COFINS", "ICMS ST", "Margem Bruta", "TAXA IPI",
                "Preço c/ IPI", "Preço TOTVS", "Coeficiente", "Lucro Bruto", "Frete", "Preço Final c/ Frete"
            ]
            for col in resultados.columns:
                st.session_state.df_editado[col] = resultados[col]


    df = st.session_state.df_editado.copy()
    colunas_principais = ["Código", "Descrição", "NCM", "CPV", "IPI", "Coeficiente"]
    if tipo_operacao == "Margem fixa":
        colunas_principais += ["Margem Bruta", "Base de cálculo", "Lucro Bruto"]
        if frete_incluso:
            colunas_principais.insert(5, "Frete")
        colunas_precos = ["Preço s/ IPI", "Preço c/ IPI", "Preço TOTVS", "Preço Final c/ Frete"]
        editavel = "Margem Bruta"  # nessa operação edita margem
    else:
        colunas_principais += ["Base de cálculo", "Lucro Bruto", "Margem Bruta"]
        if frete_incluso:
            colunas_principais.insert(5, "Frete")
        colunas_precos = ["Preço s/ IPI", "Preço c/ IPI", "Preço TOTVS", "Preço Final c/ Frete"]
        editavel = "Preço s/ IPI"  # nessa operação edita preço s/ IPI
    colunas_impostos = ["ICMS", "DIFAL", "ICMS ST(%)", "ICMS ST", "PIS", "COFINS", "TAXA IPI"]
    column_defs = []
    for c in colunas_principais:
        # só Margem Bruta é editável (texto), demais não
        editable = True if c in [editavel, "Coeficiente"] else False
        column_defs.append({
            "field": c,
            "headerName": c,
            "editable": editable,
            "filter": True,
            "resizable": True
        })
    precos_children = []
    for c in colunas_precos:
        precos_children.append({
            "field": c,
            "headerName": c,
            "editable": True if c == editavel else False,
            "filter": True,
            "resizable": True
        })
    column_defs.append({"headerName": "💲 Preços", "children": precos_children})
    impostos_children = []
    for c in colunas_impostos:
        impostos_children.append({
            "field": c,
            "headerName": c,
            "editable": False,
            "filter": True,
            "resizable": True
        })
    column_defs.append({"headerName": "📦 Impostos", "children": impostos_children})
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(resizable=True, filter=True, sortable=True)
    grid_options = gb.build()
    grid_options['columnDefs'] = column_defs
    grid_response = AgGrid(
        df,
        gridOptions=grid_options,
        enable_enterprise_modules=False,
        allow_unsafe_jscode=True,
        height=450,
        fit_columns_on_grid_load=True,
        update_mode='VALUE_CHANGED',
        data_return_mode='AS_INPUT',
        key='produtos_grid')
    if grid_response['data'] is not None:
        df_atualizado = pd.DataFrame(grid_response['data'])
        colunas_calculadas = [col for col in st.session_state.df_editado.columns
                              if col not in df_atualizado.columns]
        for coluna in colunas_calculadas:
            if coluna in st.session_state.df_editado.columns:
                df_atualizado[coluna] = st.session_state.df_editado[coluna]
        st.session_state.df_editado = df_atualizado.copy()
    # Ordenar o DataFrame para que produtos do upload apareçam primeiro
    if "Do_Upload" in st.session_state.df_editado.columns:
        st.session_state.df_editado = st.session_state.df_editado.sort_values(by="Do_Upload", ascending=False)

    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        if st.button("Salvar"):
            st.success("Alterações salvas!")
    with col_b:
        if st.button("Calcular"):
            calcular_base_de_calculo()
            st.session_state.mostrar_tabela_visualizacao = True
            st.success("Cálculo executado.")
    with col_c:
        if st.button("Limpar"):
            st.session_state.df_editado["Margem Bruta"] = ""
            st.session_state.df_editado["Base de cálculo"] = None
            st.session_state.df_editado["Lucro Bruto"] = None
            st.session_state.df_editado["Preço s/ IPI"] = None
            st.session_state.df_editado["Preço c/ IPI"] = None
            st.session_state.df_editado["Preço TOTVS"] = None
            st.session_state.df_editado["ICMS"] = None
            st.session_state.df_editado["DIFAL"] = None
            st.session_state.df_editado["ICMS ST"] = None
            st.session_state.df_editado["PIS"] = None
            st.session_state.df_editado["COFINS"] = None
            st.session_state.df_editado["TAXA IPI"] = None
            st.session_state.mostrar_tabela_visualizacao = False
            st.success("Tabela limpa.")
            st.rerun()
    with col_d:
        if st.button("Adicionar Produtos"):
            st.session_state.mostrar_formulario_adicionar = True
        if 'produtos_temp' not in st.session_state:
            st.session_state.produtos_temp = []
        # Formulário para adicionar produtos
        if st.session_state.get('mostrar_formulario_adicionar', False):
            st.markdown("---")
            st.markdown("### Adicionar Produtos")
            col1, col2, col3 = st.columns(3)
            with col1:
                novo_codigo = st.text_input("Código do Produto", key="novo_codigo")
            with col2:
                if tipo_operacao == "Margem fixa":
                    novo_valor = st.text_input("Margem Bruta (%)", key="novo_valor")
                else:
                    novo_valor = st.text_input("Preço s/ IPI", key="novo_valor")
            with col3:
                if st.button("➕ Adicionar à lista"):
                    if novo_codigo:
                        st.session_state.produtos_temp.append({
                            "codigo": str(novo_codigo).zfill(6),
                            "valor": novo_valor})
                        st.success(f"Produto {novo_codigo} adicionado à lista temporária!")
            # Mostrar lista temporária antes de salvar
            if st.session_state.produtos_temp:
                st.markdown("#### Lista Temporária")
                st.table(pd.DataFrame(st.session_state.produtos_temp))
                if st.button("💾 Salvar todos os produtos"):
                    for prod in st.session_state.produtos_temp:
                        codigo_formatado = prod["codigo"]
                        novo_valor = prod["valor"]
                        produto_info = df_base_cpv[df_base_cpv["Código"] == codigo_formatado]
                        if not produto_info.empty:
                            produto_ipi_info = df_ipi_ncm[df_ipi_ncm["Código"] == codigo_formatado]
                            icms_st_novo = 0
                            if not produto_ipi_info.empty and segmento == "Canais":
                                ncm_produto = str(produto_ipi_info["NCM"].iloc[0]).replace('.', '').replace('-','').strip().zfill(8)
                                estado_busca = estado.strip().upper()
                                df_filtrado = df_icms_st[
                                    (df_icms_st["NCM"] == ncm_produto) & (df_icms_st["ESTADO"] == estado_busca)]
                                if not df_filtrado.empty:
                                    icms_st_novo = df_filtrado.iloc[0]["Alíquota Efetiva"]
                            nova_linha = {
                                "Código": codigo_formatado,
                                "Descrição": produto_info["Descrição"].iloc[0],
                                "CPV": produto_info["CPV"].iloc[0],
                                "NCM": produto_ipi_info["NCM"].iloc[0] if not produto_ipi_info.empty else "",
                                "IPI": produto_ipi_info["IPI"].iloc[0] if not produto_ipi_info.empty else 0,
                                "ICMS ST(%)": icms_st_novo,
                                "Coeficiente": 0.2,
                                "Do_Upload": True}
                            if tipo_operacao == "Margem fixa":
                                nova_linha["Margem Bruta"] = float(novo_valor) if novo_valor else 0
                            else:
                                nova_linha["Preço s/ IPI"] = float(novo_valor) if novo_valor else 0
                            novo_df = pd.DataFrame([nova_linha])
                            st.session_state.df_editado = pd.concat([
                                st.session_state.df_editado,
                                novo_df], ignore_index=True)
                    st.session_state.produtos_temp = []
                    st.success("Todos os produtos foram adicionados com sucesso!")
    
if st.session_state.get('mostrar_tabela_visualizacao', True):
    st.markdown("---")
    st.markdown("### Tabela de Visualização")

    if "Preço s/ IPI" in st.session_state.df_editado.columns:
        df_visualizacao = st.session_state.df_editado[
            st.session_state.df_editado["Preço s/ IPI"].notna()].copy()
    else:
        # Se a coluna não existe, criar uma versão vazia do DataFrame
        df_visualizacao = st.session_state.df_editado.copy()
        # Criar a coluna "Preço s/ IPI" com valores NaN
        df_visualizacao["Preço s/ IPI"] = None

    df_visualizacao = st.session_state.df_editado.copy()
    if "Preço s/ IPI" in df_visualizacao.columns:
        df_visualizacao = df_visualizacao[df_visualizacao["Preço s/ IPI"].notna()]

    colunas_visao = ["Código", "Descrição", "CPV", "Margem Bruta", "Coeficiente",
                     "Preço s/ IPI", "Preço c/ IPI", "Preço Final c/ Frete", "Preço TOTVS"]
    colunas_existentes = [col for col in colunas_visao if col in df_visualizacao.columns]
    df_visualizacao = df_visualizacao[colunas_existentes]
    for col in ["Margem Bruta", "Coeficiente"]:
        if col in df_visualizacao.columns:
            df_visualizacao[col] = df_visualizacao[col].apply(
                lambda x: f"{(x * 100):.0f}%" if col == "Coeficiente" and pd.notna(x) and isinstance(x, (int, float))
                else f"{x:.2f}%" if pd.notna(x) and isinstance(x, (int, float))
                else x)
    for col in ["Preço s/ IPI", "Preço c/ IPI", "Preço TOTVS", "Preço Final c/ Frete"]:
        if col in df_visualizacao.columns:
            df_visualizacao[col] = df_visualizacao[col].apply(
                lambda x: f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if pd.notna(
                    x) and isinstance(x, (int, float)) else x)
    st.dataframe(df_visualizacao, use_container_width=True, hide_index=True)

# Função para obter os preços do TOTVS
def obter_preco_totvs_api():
    try:
        url = "http://ambartech134415.protheus.cloudtotvs.com.br:1807/rest/api/v1/calccomponentesorc2022/tabelapreco"
        response = requests.get(url, auth=HTTPBasicAuth("ambar.integracao", "!ambar@2025int"))
        response.raise_for_status()
        dados_api = response.json()
        df_preco = pd.DataFrame(dados_api)

        # Filtrar apenas colunas necessárias
        colunas_desejadas = ["DA1_CODTAB", "DA1_CODPRO", "DA1_PRCVEN"]
        df_preco = df_preco[colunas_desejadas]

        # Filtrar apenas a tabela de preço P01
        df_preco = df_preco[df_preco["DA1_CODTAB"] == "P01"]

        df_preco.rename(columns={ "DA1_CODPRO": "Código", "DA1_PRCVEN": "Preço do TOTVS" }, inplace=True)
        df_preco["Código"] = df_preco["Código"].astype(str).str.zfill(6)
        df_preco["Preço do TOTVS"] = pd.to_numeric(df_preco["Preço do TOTVS"], errors="coerce")
        return df_preco[["Código", "Preço do TOTVS"]]
    except Exception as e: 
        st.error(f"Erro ao obter preços da API TOTVS: {e}") 
        return pd.DataFrame(columns=["Código", "Preço do TOTVS"])
    
#analise de tabela
with st.sidebar:
    st.markdown("---")
    if "mostrar_analise_tabela" not in st.session_state:
        st.session_state.mostrar_analise_tabela = False

    if st.button("📊 Análise de tabela"):
        st.session_state.mostrar_analise_tabela = not st.session_state.mostrar_analise_tabela

if st.session_state.mostrar_analise_tabela:
    try:
        df_visualizacao = st.session_state.df_editado.copy()
        if "Preço s/ IPI" in df_visualizacao.columns:
            df_produtos_visualizacao = df_visualizacao[df_visualizacao["Preço s/ IPI"].notna()].copy()
        else:
            df_produtos_visualizacao = df_visualizacao.copy()

        # Obtém preços do TOTVS
        df_preco_totvs = obter_preco_totvs_api()
        df_produtos_visualizacao["Código"] = df_produtos_visualizacao["Código"].astype(str).str.zfill(6)

        # Faz merge com os preços do TOTVS
        df_analise = pd.merge(
            df_produtos_visualizacao[["Código", "Descrição", "Preço s/ IPI", "CPV", "Margem Bruta"]],
            df_preco_totvs,
            on="Código",
            how="left"
        )

        df_faturamento = obter_faturamento_sql()
        if not df_faturamento.empty and "Código" in df_faturamento.columns:
            # Exemplo de agregação do faturamento (total dos últimos 12 meses por produto)
            if "C6_VALOR" in df_faturamento.columns:
                df_faturamento_agg = df_faturamento.groupby("Código")["C6_VALOR"].sum().reset_index()
                #df_faturamento_agg.rename(columns={"C6_VALOR": "Faturamento 12M"}, inplace=True)

                df_analise = pd.merge(df_analise, df_faturamento_agg, on="Código", how="left")
            else:
                st.warning("Coluna de valor de faturamento (C6_VALOR) não encontrada em SC6G10.")
        else:
            st.warning("Nenhum dado de faturamento retornado do SQL Server.")

        # Cálculos
        df_analise["Preço Acordo s/ Juros"] = df_analise["Preço s/ IPI"] / 1.0047
        df_analise["Desconto_num"] = 1 - (df_analise["Preço Acordo s/ Juros"] / df_analise["Preço do TOTVS"])
        df_analise["Desconto"] = df_analise["Desconto_num"].apply(
            lambda x: f"{x:.2%}" if pd.notna(x) and isinstance(x, (int, float)) else "N/A"
        )

        # Formatação monetária
        for col in ["Preço s/ IPI", "Preço do TOTVS", "Preço Acordo s/ Juros", "CPV"]:
            if col in df_analise.columns:
                df_analise[col] = df_analise[col].apply(
                    lambda x: f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    if pd.notna(x) and isinstance(x, (int, float))
                    else "N/A"
                )

        # Formatação de margem
        if "Margem Bruta" in df_analise.columns:
            df_analise["Margem Bruta"] = df_analise["Margem Bruta"].apply(
                lambda x: f"{x:.2f}%" if pd.notna(x) and isinstance(x, (int, float)) else "N/A"
            )

        # Função para colorir a coluna de desconto
        def colorir_desconto(val):
            if pd.isna(val):
                return ''
            elif val < 0:
                return 'background-color: #ADD8E6; color: black;'   # azul
            elif 0 <= val < 0.07:
                return 'background-color: #008000; color: white;'   # verde
            elif 0.07 <= val < 0.15:
                return 'background-color: #FFFF00; color: black;'   # amarelo
            elif 0.15 <= val < 0.50:
                return 'background-color: #FF9999; color: black;'   # vermelho claro
            elif val >= 0.50:
                return 'background-color: #880808; color: white;'   # vermelho escuro
            else:
                return ''

        # Aplica o estilo de cor com base no valor numérico
        styled_df = (
            df_analise.drop(columns=["Desconto_num"], errors="ignore")
            .style.apply(
                lambda s: [colorir_desconto(v) for v in df_analise["Desconto_num"]],
                subset=["Desconto"]
            )
        )

        # Exibe a tabela
        st.subheader("📊 Análise de Tabela")
        st.dataframe(styled_df, use_container_width=True, hide_index=True)

        # Armazena para uso posterior
        st.session_state.df_analise_tabela = df_analise.drop(columns=["Desconto_num"], errors="ignore").copy()

    except Exception as e:
        st.error(f"Erro ao gerar análise da tabela: {e}")


with st.sidebar:
    st.markdown("---")
    st.header("📑 Acordo")
    # Botão para controlar a exibição do formulário de acordo
    if 'mostrar_formulario_acordo' not in st.session_state:
        st.session_state.mostrar_formulario_acordo = False
    if st.button("Fazer Acordo", key="gerar_acordo_btn"):
        st.session_state.mostrar_formulario_acordo = not st.session_state.mostrar_formulario_acordo
    # Exibir campos do acordo apenas se o botão foi clicado
    if st.session_state.mostrar_formulario_acordo:
        planilha_imagens_url = "https://docs.google.com/spreadsheets/d/1XgI23B79U50I2mhw1Wfgd9cfE4PZoDq1e793i7zoBcU/edit"
        sem_imagem_base64 = ""
        sem_imagem_path = Path("sem_imagem.png")
        if sem_imagem_path.exists():
            with open(sem_imagem_path, "rb") as img_file:
                sem_imagem_base64 = base64.b64encode(img_file.read()).decode("utf-8")
        st.sidebar.title("Dados da Proposta")
        # Identificar o tipo de tabela
        if tipo_operacao == "Preço final fixo":
            tipo_tabela = "Tabela por Preço"
        else:
            tipo_tabela = "Tabela por Margem"
        st.write(f"**Tipo de Tabela:** {tipo_tabela}")
        # Campos do formulário
        tipo_documento = st.sidebar.selectbox("Tipo de documento", ["Acordo Corporativo", "Proposta de negociação"])
        cliente = st.sidebar.text_input("Nome do cliente*")
        uf = st.sidebar.multiselect("UF*",
                                        ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
                                         "PA",
                                         "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"])
        tipo_cliente = st.sidebar.selectbox("Tipo de cliente*", ["Construtora", "Revenda", "Industrialização"])
        st.write(f"**Condição de Pagamento:** {cond_pagamento}")
        st.write(f"**Frete:** {'Incluso' if frete_incluso else 'Não incluso'}")

        pedido_minimo = st.sidebar.text_input("Pedido mínimo (R$)")
        potencial_faturamento = st.sidebar.text_input("Potencial de faturamento (R$)")
        duracao_acordo = st.sidebar.text_input("Duração do acordo")
        data_proposta = st.sidebar.text_input("Data da Proposta")
        observacoes = st.sidebar.text_area("Observações")

        colunas_disponiveis = {"Código": "Código", "Descrição": "Descrição", "NCM": "NCM", "Imagem": "Imagem", "Preço sem IPI": "Preço s/ IPI", "IPI (%)": "IPI (%)", "Preço com IPI": "Preço c/ IPI"}
        # Salvar a escolha no session_state para usar também no PDF
        if "colunas_exibidas" not in st.session_state:
            st.session_state.colunas_exibidas = list(colunas_disponiveis.keys())

        st.session_state.colunas_exibidas = st.sidebar.multiselect(
            "Colunas da tabela de itens",
            list(colunas_disponiveis.keys()),
            default=st.session_state.colunas_exibidas
        )
        # Botão para controlar a exibição da prévia
        if 'mostrar_previa' not in st.session_state:
            st.session_state.mostrar_previa = False
        if st.sidebar.button("Gerar Prévia"):
            # Verificar campos obrigatórios antes de mostrar prévia
            if not cliente:
                st.sidebar.warning("Preencha o nome do cliente para gerar a prévia")
            elif not uf:
                st.sidebar.warning("Selecione pelo menos uma UF para gerar a prévia")
            elif not tipo_cliente:
                st.sidebar.warning("Selecione o tipo de cliente para gerar a prévia")
            else:
                st.session_state.mostrar_previa = not st.session_state.mostrar_previa

@st.cache_data
def carregar_imagens():
    try:
        # Caminho local do arquivo Excel
        caminho_excel = Path("Base de imagens (1).xlsx")

        if not caminho_excel.exists():
            st.error("❌ Arquivo 'imagens_produtos.xlsx' não encontrado na pasta do projeto.")
            st.stop()

        # Lê a planilha Excel da aba 'Página1'
        df = pd.read_excel(caminho_excel, sheet_name="Página1", usecols=[0, 1], header=None)
        df.columns = ["código certo", "valor"]  # Coluna A = código, Coluna B = link da imagem

        # Remove linhas vazias
        df = df.dropna(subset=["código certo", "valor"])

        # Cria o dicionário código → imagem
        imagens_dict = dict(zip(df["código certo"].astype(str).str.zfill(6), df["valor"]))

        return imagens_dict

    except Exception as e:
        st.error(f"Erro ao carregar imagens do Excel: {e}")
        st.stop()

imagens_dict = carregar_imagens()
def gerar_tabela_acordo():
    # Verificar se há dados na tabela de visualização
    if 'df_editado' not in st.session_state or st.session_state.df_editado.empty:
        st.warning("Nenhum produto disponível para gerar o acordo.")
        return None
    df_acordo = st.session_state.df_editado[
        st.session_state.df_editado["Preço s/ IPI"].notna()].copy()
    if df_acordo.empty:
        st.warning("Nenhum produto selecionado para o acordo.")
        return None
    if "IPI" in df_acordo.columns:
        df_acordo['IPI (%)'] = df_acordo['IPI']
    for col in ['Preço s/ IPI', 'Preço c/ IPI']:
        if col in df_acordo.columns:
            df_acordo[col] = df_acordo[col].apply(
                lambda x: f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                if pd.notna(x) and isinstance(x, (int, float)) else "R$ 0,00")
    def obter_imagem(codigo):
        codigo_str = str(codigo).zfill(6)
        if codigo_str in imagens_dict:
            return f'<img src="{imagens_dict[codigo_str]}" width="80">'
        elif sem_imagem_base64:
            return f'<img src="data:image/png;base64,{sem_imagem_base64}" width="80">'
        else:
            return "Sem imagem"
    if "Código" in df_acordo.columns:
        df_acordo['Imagem'] = df_acordo['Código'].apply(obter_imagem)
    # Utiliza sempre as colunas selecionadas no multiselect
    colunas_disponiveis = {
        "Código": "Código",
        "Descrição": "Descrição",
        "NCM": "NCM",
        "Imagem": "Imagem",
        "Preço sem IPI": "Preço s/ IPI",
        "IPI (%)": "IPI (%)",
        "Preço com IPI": "Preço c/ IPI"
    }
    colunas_usuario = st.session_state.get("colunas_exibidas", list(colunas_disponiveis.keys()))
    colunas_reais = [colunas_disponiveis[c] for c in colunas_usuario if colunas_disponiveis[c] in df_acordo.columns]

    df_acordo_final = df_acordo[colunas_reais]
    return df_acordo_final
# Gerar prévia do acordo (aparece/desaparece ao clicar)
if st.session_state.mostrar_formulario_acordo and st.session_state.mostrar_previa:
    # Verificar se campos obrigatórios estão preenchidos
    if not cliente:
        st.warning("Preencha o nome do cliente para visualizar a prévia")
    elif not uf:
        st.warning("Selecione pelo menos uma UF para visualizar a prévia")
    elif not tipo_cliente:
        st.warning("Selecione o tipo de cliente para visualizar a prévia")
    else:
        df_acordo = gerar_tabela_acordo()
        if df_acordo is not None:
            st.markdown("---")
            st.subheader("📋 Prévia do Acordo")

            # Exibir informações do cabeçalho conforme layout do PDF
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.write(f"**Cliente:** {cliente}")
            with col2:
                st.write(f"**UF:** {', '.join(uf) if uf else 'Não informado'}")
            with col3:
                st.write(f"**Tipo:** {tipo_cliente}")
            with col4:
                st.write(f"**Potencial:** {potencial_faturamento}")

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.write(f"**Condições:** {cond_pagamento}")
            with col2:
                st.write(f"**Frete Incluso:** {'Sim' if frete_incluso else 'Não'}")
            with col3:
                st.write(f"**Duração:** {duracao_acordo}")
            with col4:
                st.write(f"**Pedido Mínimo:** {pedido_minimo}")

            col1, col2 = st.columns([1, 2])
            with col1:
                st.write(f"**Data da proposta:** {data_proposta}")
            with col2:
                st.write(f"**Observações:** {observacoes}")

            # Converter DataFrame para HTML mantendo as imagens
            html_tabela = df_acordo.to_html(escape=False, index=False, classes='tabela-itens')
            # Exibir tabela
            st.components.v1.html(html_tabela, height=400, scrolling=True)

html_template = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Proposta Comercial - Polar</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 3px 0px;
            color: #2C3E50;
            background-color: white;
        }
        .topo {
            text-align: center;
            margin-bottom: 5px;
        }
        .topo img {
            width: 100px;
            margin-bottom: 4px;
        }
        .topo h2 {
            color: #1A5276;
            font-size: 16px;
            margin: 4px 0;
        }
        .razao {
            font-weight: bold;
            color: #21618C;
            font-size: 14px;
        }
        .info {
            font-size: 12px;
            color: #555;
        }
        .header {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }
        .header td {
            padding: 6px 8px;
            border: 1px solid #ccc;
            background-color: #1A5276;
            font-size: 12px;
            color: white;
        }
        .tabela-itens {
            width: 100%;
            border-collapse: collapse;
            margin-top: 30px;
            font-size: 12px;
            table-layout: fixed; /* força largura proporcional */
        }
        .tabela-itens th, .tabela-itens td {
            border: 1px solid #ccc;
            padding: 6px;
            text-align: left;
            vertical-align: middle;
        }
        .tabela-itens th {
            background-color: #1A5276;
            color: white;
            font-weight: bold;
        }
        /* Ajuste das larguras */
        .tabela-itens th:nth-child(1),
        .tabela-itens td:nth-child(1) {
            width: 10%; /* Código */
        }
        .tabela-itens th:nth-child(2),
        .tabela-itens td:nth-child(2) {
            width: 50%; /* Descrição (mais largo) */
        }
        .tabela-itens th:nth-child(3),
        .tabela-itens td:nth-child(3) {
            width: 15%; /* Quantidade */
        }
        .tabela-itens th:nth-child(4),
        .tabela-itens td:nth-child(4) {
            width: 15%; /* Valor Unitário */
        }
        .tabela-itens th:nth-child(5),
        .tabela-itens td:nth-child(5) {
            width: 15%; /* Total */
        }
        .tabela-itens img {
            max-width: 80px;
            height: auto;
            display: block;
            margin: 0 auto;
        }
        .divider {
            border-top: 1px solid #ccc;
            margin: 10px 0;
        }
    </style>
</head>
<body>
    <div class="topo">
        <img src="data:image/png;base64,{{logo_base64}}">
        <h2>{{tipo_documento}}</h2>
        <div class="razao">POLAR INDUSTRIA DE PLASTICOS S/A</div>
        <div class="info">
            RODOVIA WASHINGTON LUZ KM 225 + 736MJ 50 GALPÃO 02 - SÃO CARLOS - SP<br>
            CNPJ: 17.298.395/0001-95<br>
            www.polar.com.br
        </div>
    </div>

    <table class="header">
        <tr>
            <td><strong>Cliente:</strong> {{cliente}}</td>
            <td><strong>UF:</strong> {{uf}}</td>
            <td><strong>Tipo:</strong> {{tipo_cliente}}</td>
            <td><strong>Potencial:</strong> {{potencial_faturamento}}</td>
        </tr>
        <tr>
            <td><strong>Condições:</strong> {{condicoes_pagamento}}</td>
            <td><strong>Frete Incluso:</strong> {{frete_incluso}}</td>
            <td><strong>Duração:</strong> {{duracao_acordo}}</td>
            <td><strong>Pedido Mínimo:</strong> {{pedido_minimo}}</td>
        </tr>
        <tr>
            <td colspan="4"><strong>Data da proposta:</strong> {{data_proposta}}</td>
        </tr>
        <tr>
            <td colspan="4"><strong>Observações:</strong> {{observacoes}}</td>
        </tr>
    </table>
    {{tabela_itens}}
</body>
</html>
"""

CAMINHO_JSON = Path("historico_acordos.json")
def salvar_historico_json():
    try:
        historico_serializavel = []
        for acordo in st.session_state.historico_acordos:
            acordo_copy = acordo.copy()
            if isinstance(acordo_copy["tabela_itens"], pd.DataFrame):
                tabela_filtrada = acordo_copy["tabela_itens"].dropna(how="any").copy()
                acordo_copy["tabela_itens"] = tabela_filtrada.to_dict(orient="records")
            historico_serializavel.append(acordo_copy)

        with open(CAMINHO_JSON, "w", encoding="utf-8") as f:
            json.dump(historico_serializavel, f, ensure_ascii=False, indent=4)
    except Exception as e:
        st.sidebar.error(f"Erro ao salvar histórico em JSON: {e}")

def carregar_historico_json():
    if CAMINHO_JSON.exists():
        try:
            with open(CAMINHO_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            historico = []
            for acordo in data:
                acordo_copy = acordo.copy()
                acordo_copy["tabela_itens"] = pd.DataFrame(acordo_copy["tabela_itens"])
                historico.append(acordo_copy)
            return historico
        except Exception as e:
            st.sidebar.error(f"Erro ao carregar histórico em JSON: {e}")
            return []
    return []
if "historico_acordos" not in st.session_state:
    st.session_state.historico_acordos = carregar_historico_json()
if st.sidebar.button("🔄 Carregar acordo salvo"):
    if st.session_state.historico_acordos:
        opcoes = [f"{i+1} - {a['cliente']} ({a['data_proposta']})"
                  for i, a in enumerate(st.session_state.historico_acordos)]
        escolha = st.sidebar.selectbox("Selecione o acordo:", opcoes)
        if escolha:
            idx = int(escolha.split(" - ")[0]) - 1
            acordo = st.session_state.historico_acordos[idx]
            cliente = acordo["cliente"]
            uf = acordo["uf"]
            tipo_cliente = acordo["tipo_cliente"]
            potencial_faturamento = acordo["potencial_faturamento"]
            cond_pagamento = acordo["condicoes_pagamento"]
            frete_incluso = acordo["frete_incluso"]
            duracao_acordo = acordo["duracao_acordo"]
            pedido_minimo = acordo["pedido_minimo"]
            data_proposta = acordo["data_proposta"]
            observacoes = acordo["observacoes"]
            st.session_state.df_editado = acordo["tabela_itens"].copy()
            st.sidebar.success(f"✅ Acordo {escolha} carregado com sucesso!")
baixar_pdf = st.sidebar.button("Download do PDF")

if baixar_pdf and st.session_state.mostrar_formulario_acordo:
    if not cliente:
        st.sidebar.warning("Preencha o nome do cliente para gerar o PDF")
    elif not uf:
        st.sidebar.warning("Selecione pelo menos uma UF para gerar o PDF")
    elif not tipo_cliente:
        st.sidebar.warning("Selecione o tipo de cliente para gerar o PDF")
    else:
        try:
            df_acordo = gerar_tabela_acordo()
            if df_acordo is not None:
                novo_acordo = {
                    "cliente": cliente,
                    "uf": uf,
                    "tipo_cliente": tipo_cliente,
                    "potencial_faturamento": potencial_faturamento,
                    "condicoes_pagamento": cond_pagamento,
                    "frete_incluso": frete_incluso,
                    "duracao_acordo": duracao_acordo,
                    "pedido_minimo": pedido_minimo,
                    "data_proposta": str(data_proposta),  # garantir compatibilidade JSON
                    "observacoes": observacoes,
                    "tabela_itens": st.session_state.df_editado.copy()}
                st.session_state.historico_acordos.append(novo_acordo)
                salvar_historico_json()
                # Garante que só as colunas selecionadas pelo usuário vão para o PDF
                colunas_usuario = st.session_state.get("colunas_exibidas", list(df_acordo.columns))
                df_acordo_pdf = df_acordo[[col for col in colunas_usuario if col in df_acordo.columns]]
                #html_tabela = df_acordo_pdf.to_html(escape=False, index=False, classes='tabela-itens', border=0, justify='left')
                html_tabela = df_acordo.to_html(escape=False, index=False, classes='tabela-itens', border=0, justify='left')

                logo_path = Path("logo_polar.png")
                if logo_path.exists():
                    with open(logo_path, "rb") as logo_file:
                        logo_base64 = base64.b64encode(logo_file.read()).decode("utf-8")
                else:
                    logo_base64 = ""
                html_final = html_template.replace("{{logo_base64}}", logo_base64)
                html_final = html_final.replace("{{tipo_documento}}", tipo_documento)
                html_final = html_final.replace("{{cliente}}", cliente)
                html_final = html_final.replace("{{uf}}", ", ".join(uf))
                html_final = html_final.replace("{{tipo_cliente}}", tipo_cliente)
                html_final = html_final.replace("{{potencial_faturamento}}", potencial_faturamento or "Não informado")
                html_final = html_final.replace("{{condicoes_pagamento}}", cond_pagamento)
                html_final = html_final.replace("{{frete_incluso}}", "Sim" if frete_incluso else "Não")
                html_final = html_final.replace("{{duracao_acordo}}", duracao_acordo or "Não definida")
                html_final = html_final.replace("{{pedido_minimo}}", pedido_minimo or "Não definido")
                html_final = html_final.replace("{{data_proposta}}", str(data_proposta))
                html_final = html_final.replace("{{observacoes}}", observacoes or "Nenhuma")
                html_final = html_final.replace("{{tabela_itens}}", html_tabela)
                from xhtml2pdf import pisa
                import io
                pdf_buffer = io.BytesIO()
                pisa_status = pisa.CreatePDF(html_final, dest=pdf_buffer)
                if pisa_status.err:
                    st.sidebar.error("Erro ao criar PDF")
                else:
                    pdf_bytes = pdf_buffer.getvalue()
                    nome_arquivo = f"acordo_{cliente}_{data_proposta}.pdf".replace(" ", "_")
                    st.sidebar.download_button(
                        label="⬇️ Baixar Acordo em PDF",
                        data=pdf_bytes,
                        file_name=nome_arquivo,
                        mime="application/pdf")
                    st.sidebar.success("PDF gerado e salvo no histórico com sucesso! ✅")
        except Exception as e:

            st.sidebar.error(f"Erro ao gerar PDF: {str(e)}")
