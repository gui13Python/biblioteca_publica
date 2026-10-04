"""
Importa o catálogo oficial do Project Gutenberg para o SQLite local (livros.db).

Rode no SEU PC (não na Vercel). Ele cria o livros.db e também o livros.db.gz,
que é o arquivo que vai para o git/Vercel (menor: ~10 MB).

    python importar_catalogo.py                 # importa só se estiver faltando ou desatualizado
    python importar_catalogo.py --atualizar     # força baixar e importar de novo
    python importar_catalogo.py meu_arquivo.csv # importa um CSV que você já tem

Depois de atualizar: envie o novo livros.db.gz para o git e a Vercel publica sozinha.
"""
import csv
import gzip
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

import requests

BASE = Path(__file__).parent
DB = BASE / "livros.db"
CSV_LOCAL = BASE / "pg_catalog.csv"
URL = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv"
CABECALHOS = {"User-Agent": "BibliotecaLivre/1.0"}


def normalizar(txt):
    txt = unicodedata.normalize("NFKD", txt or "")
    return "".join(c for c in txt if not unicodedata.combining(c)).lower()


# ---------------------------------------------------------------- metadados
def conectar():
    return sqlite3.connect(DB, timeout=30)


def ler_meta(chave):
    try:
        con = conectar()
        try:
            r = con.execute("SELECT valor FROM meta WHERE chave=?", (chave,)).fetchone()
        finally:
            con.close()
        return r[0] if r else None
    except sqlite3.OperationalError:
        return None


def guardar_meta(chave, valor):
    con = conectar()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT)")
        con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (chave, valor))
        con.commit()
    finally:
        con.close()


def tabela_existe():
    try:
        con = conectar()
        try:
            return con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='livros'"
            ).fetchone() is not None
        finally:
            con.close()
    except sqlite3.OperationalError:
        return False


# ---------------------------------------------------------------- download / importação
def assinatura_remota():
    """Pergunta ao Gutenberg (sem baixar o arquivo) se o catálogo mudou."""
    try:
        r = requests.head(URL, timeout=20, allow_redirects=True, headers=CABECALHOS)
        r.raise_for_status()
    except requests.RequestException:
        return None
    h = r.headers
    return f"{h.get('ETag', '')}|{h.get('Last-Modified', '')}|{h.get('Content-Length', '')}"


def baixar_csv():
    print("[catálogo] baixando do Project Gutenberg (alguns MB)...")
    tmp = CSV_LOCAL.with_suffix(".tmp")
    with requests.get(URL, stream=True, timeout=60, headers=CABECALHOS) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for bloco in r.iter_content(1 << 16):
                f.write(bloco)
    tmp.replace(CSV_LOCAL)


def importar(caminho):
    """Monta a tabela nova ao lado e troca no final: o site nunca fica sem catálogo."""
    con = conectar()
    try:
        con.execute("DROP TABLE IF EXISTS livros_novo")
        con.execute("""CREATE TABLE livros_novo (
            id INTEGER PRIMARY KEY, titulo TEXT, autores TEXT, idiomas TEXT,
            assuntos TEXT, busca TEXT, temas TEXT)""")

        linhas = []
        with open(caminho, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("Type") or "Text") != "Text":
                    continue
                try:
                    livro_id = int(row.get("Text#") or row.get("ID"))
                except (TypeError, ValueError):
                    continue
                titulo = re.sub(r"\s*[\r\n]+\s*", " - ", (row.get("Title") or "").strip())
                autores = (row.get("Authors") or "").strip()
                idiomas = [i.strip() for i in re.split(r"[;,]", row.get("Language") or "") if i.strip()]
                assuntos = (row.get("Subjects") or "").strip()
                estantes = (row.get("Bookshelves") or "").strip()
                linhas.append((
                    livro_id, titulo, autores,
                    ";" + ";".join(idiomas) + ";",
                    assuntos,
                    normalizar(titulo + " " + autores),
                    normalizar(assuntos + " " + estantes),
                ))

        con.executemany("INSERT OR REPLACE INTO livros_novo VALUES (?,?,?,?,?,?,?)", linhas)
        con.execute("DROP TABLE IF EXISTS livros")
        con.execute("ALTER TABLE livros_novo RENAME TO livros")
        con.commit()
    finally:
        con.close()
    print(f"[catálogo] pronto: {len(linhas)} livros importados.")
    return len(linhas)


def gerar_pacote():
    """Cria o livros.db.gz só com a tabela de livros (arquivo leve para o git/Vercel)."""
    leve = BASE / "livros_leve.tmp"
    leve.unlink(missing_ok=True)
    con = sqlite3.connect(leve)
    try:
        con.execute("ATTACH DATABASE ? AS origem", (str(DB),))
        con.execute("""CREATE TABLE livros (
            id INTEGER PRIMARY KEY, titulo TEXT, autores TEXT, idiomas TEXT,
            assuntos TEXT, busca TEXT, temas TEXT)""")
        con.execute("INSERT INTO livros SELECT id, titulo, autores, idiomas, assuntos, busca, temas FROM origem.livros")
        con.commit()
        con.execute("DETACH DATABASE origem")
        con.execute("VACUUM")
    finally:
        con.close()
    with open(leve, "rb") as a, gzip.open(BASE / "livros.db.gz", "wb", 9) as b:
        shutil.copyfileobj(a, b)
    leve.unlink(missing_ok=True)
    print("[catálogo] livros.db.gz gerado (é esse arquivo que vai para o git).")


def atualizar_se_preciso(forcar=False):
    """Importa se o catálogo não existe, se o Gutenberg publicou mudanças ou se forcar=True.
    Retorna True se importou."""
    assinatura = assinatura_remota()
    mudou = assinatura is not None and assinatura != ler_meta("assinatura")
    if not (forcar or mudou or not tabela_existe()):
        return False
    baixar_csv()
    importar(CSV_LOCAL)
    gerar_pacote()
    CSV_LOCAL.unlink(missing_ok=True)  # o CSV já foi para o banco; libera espaço
    guardar_meta("assinatura", assinatura or "")
    guardar_meta("importado_em", str(time.time()))
    return True


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        importar(args[0])
        gerar_pacote()
    else:
        feito = atualizar_se_preciso(forcar="--atualizar" in sys.argv)
        if not feito:
            print("[catálogo] já está atualizado.")
