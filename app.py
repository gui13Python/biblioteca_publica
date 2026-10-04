"""
Biblioteca Livre - site de livros gratuitos (domínio público)
Catálogo: Project Gutenberg, guardado em SQLite (somente leitura) dentro do próprio projeto.

Pronto para a Vercel: o site NÃO grava nada em disco.
  - catálogo: livros.db.gz (vai junto no git) -> descompactado em /tmp na primeira visita
  - downloads: token assinado (sem banco)
  - livros ocultos: arquivo ocultos.txt

Rodar no seu PC:
    pip install -r requirements.txt
    python app.py
Abrir: http://127.0.0.1:5000
"""
import gzip
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_from_directory)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

BASE = Path(__file__).parent
DB_LOCAL = BASE / "livros.db"        # usado se existir (ex.: no seu PC)
DB_PACOTE = BASE / "livros.db.gz"    # vai no git; descompactado em /tmp quando precisa
ESPERA = 10           # segundos que o usuário precisa aguardar
TOKEN_VALIDADE = 300  # segundos que o token fica válido após o tempo de espera
POR_PAGINA = 32
# Chave que assina os tokens de download. Na Vercel, você pode definir a variável
# de ambiente SECRET_KEY (Settings > Environment Variables) para ter a sua própria.
SECRET_KEY = os.environ.get("SECRET_KEY", "biblioteca-livre-troque-esta-chave")

# ---- dados do site (usados nas páginas Sobre, Contato, Privacidade etc.) ----
SITE_NOME = "Biblioteca Livre"
MARCA = "PyTK Solutions"
EMAIL_CONTATO = "gui13pythonccna@gmail.com"  # e-mail exibido nas páginas Contato, Privacidade etc.
TIKTOKS = [
    ("@codefortheweb", "https://www.tiktok.com/@codefortheweb"),
    ("@solutionspytk1", "https://www.tiktok.com/@solutionspytk1"),
]
ATUALIZADO_EM = "4 de outubro de 2026"  # data exibida nas políticas; atualize se mudar o texto
PAGINAS = {  # endereço -> arquivo
    "sobre": "sobre.html",
    "contato": "contato.html",
    "privacidade": "privacidade.html",
    "termos": "termos.html",
    "direitos-autorais": "direitos.html",
}

# chave -> (rótulo exibido, endereço de download no Gutenberg)
FORMATOS = {
    "epub": ("EPUB", "https://www.gutenberg.org/ebooks/{id}.epub3.images"),
    "kindle": ("Kindle (AZW3)", "https://www.gutenberg.org/ebooks/{id}.kf8.images"),
    "html": ("Leitura online (HTML)", "https://www.gutenberg.org/cache/epub/{id}/pg{id}-images.html"),
    "txt": ("TXT", "https://www.gutenberg.org/ebooks/{id}.txt.utf-8"),
}

# rótulo exibido -> termo buscado nos assuntos/estantes do Gutenberg (em inglês)
CATEGORIAS = {
    "Romance": "romance",
    "Literatura clássica": "literature",
    "Aventura / Ação": "adventure",
    "Ficção científica": "science fiction",
    "Mistério / Policial": "detective",
    "Terror": "horror",
    "Fantasia": "fantasy",
    "Humor": "humor",
    "Poesia": "poetry",
    "Teatro": "drama",
    "Infantil": "children",
    "Filosofia": "philosophy",
    "História": "history",
    "Religião": "religion",
    "Ciência": "science",
    "Finanças / Economia": "economics",
    "Negócios": "business",
    "Psicologia": "psychology",
    "Cérebro / Neurologia": "brain",
    "Autoajuda / Sucesso": "success",
}

# Template e arquivos estáticos na mesma pasta (estrutura plana)
app = Flask(__name__, template_folder=str(BASE), static_folder=None)


@app.context_processor
def variaveis_globais():
    return dict(site_nome=SITE_NOME, marca=MARCA, email=EMAIL_CONTATO, tiktoks=TIKTOKS,
                atualizado_em=ATUALIZADO_EM, ano=datetime.now().year)


# ---------------------------------------------------------------- utilitários
def normalizar(txt):
    txt = unicodedata.normalize("NFKD", txt or "")
    return "".join(c for c in txt if not unicodedata.combining(c)).lower()


def limpar_autor(a):
    """'Austen, Jane, 1775-1817' -> 'Jane Austen'"""
    partes = [p.strip() for p in a.split(",") if p.strip()]
    partes = [p for p in partes if not re.match(r"^(\d|-|active|fl\.|ca\.)", p, re.I)]
    if len(partes) >= 2:
        return f"{partes[1]} {partes[0]}"
    return partes[0] if partes else a.strip()


def livro_dict(r):
    autores = ", ".join(limpar_autor(a) for a in (r["autores"] or "").split(";") if a.strip())
    assuntos = [a.strip() for a in (r["assuntos"] or "").replace("--", ";").split(";") if a.strip()]
    return {
        "id": r["id"],
        "titulo": r["titulo"] or "Sem título",
        "autores": autores or "Autor desconhecido",
        "capa": f"https://www.gutenberg.org/cache/epub/{r['id']}/pg{r['id']}.cover.medium.jpg",
        "idiomas": ", ".join(i for i in (r["idiomas"] or "").split(";") if i),
        "assuntos": assuntos[:6],
        "formatos": [{"chave": k, "rotulo": v[0]} for k, v in FORMATOS.items()],
    }


# ---------------------------------------------------------------- banco (somente leitura)
_trava_db = threading.Lock()
_db_caminho = None


def caminho_db():
    """Acha o livros.db; se só existir o .gz, descompacta uma vez em /tmp (único lugar gravável na Vercel)."""
    global _db_caminho
    if _db_caminho is not None and _db_caminho.exists():
        return _db_caminho
    with _trava_db:
        if DB_LOCAL.exists():
            _db_caminho = DB_LOCAL
        elif DB_PACOTE.exists():
            destino = Path(tempfile.gettempdir()) / "biblioteca_livros.db"
            if not destino.exists() or destino.stat().st_size == 0:
                tmp = destino.with_name(f"{destino.name}.{os.getpid()}.tmp")
                with gzip.open(DB_PACOTE, "rb") as a, open(tmp, "wb") as b:
                    shutil.copyfileobj(a, b)
                tmp.replace(destino)
            _db_caminho = destino
        else:
            _db_caminho = None
    return _db_caminho


def conectar():
    caminho = caminho_db()
    if caminho is None:
        raise sqlite3.OperationalError("catálogo não encontrado")
    con = sqlite3.connect(f"{caminho.as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def ids_ocultos():
    """Lê ocultos.txt (um número de livro por linha; '#' começa comentário)."""
    try:
        linhas = (BASE / "ocultos.txt").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    ids = []
    for linha in linhas:
        linha = linha.split("#")[0].strip()
        if linha.isdigit():
            ids.append(int(linha))
    return ids


# ---------------------------------------------------------------- páginas
@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    idioma = request.args.get("idioma", "").strip()
    categoria = request.args.get("categoria", "").strip()
    pagina = max(request.args.get("pagina", 1, type=int), 1)

    where, args = [], []
    ocultos = ids_ocultos()
    if ocultos:
        where.append(f"id NOT IN ({','.join('?' * len(ocultos))})")
        args += ocultos
    for palavra in normalizar(q).split():
        where.append("busca LIKE ?")
        args.append(f"%{palavra}%")
    if idioma:
        where.append("idiomas LIKE ?")
        args.append(f"%;{idioma};%")
    if categoria in CATEGORIAS:
        where.append("temas LIKE ?")
        args.append(f"%{CATEGORIAS[categoria]}%")
    sql_where = " AND ".join(where) or "1=1"

    contexto = dict(q=q, idioma=idioma, categoria=categoria, categorias=CATEGORIAS,
                    pagina=pagina, livros=[], total=0, tem_proxima=False,
                    sem_catalogo=False, erro=False)
    try:
        with closing(conectar()) as con:
            total = con.execute(f"SELECT COUNT(*) FROM livros WHERE {sql_where}", args).fetchone()[0]
            linhas = con.execute(
                f"SELECT * FROM livros WHERE {sql_where} ORDER BY id LIMIT ? OFFSET ?",
                args + [POR_PAGINA, (pagina - 1) * POR_PAGINA]).fetchall()
    except sqlite3.OperationalError:
        contexto["sem_catalogo"] = True
        return render_template("index.html", **contexto)

    contexto.update(livros=[livro_dict(r) for r in linhas], total=total,
                    tem_proxima=total > pagina * POR_PAGINA)
    return render_template("index.html", **contexto)


def buscar_livro(livro_id):
    if livro_id in ids_ocultos():
        return None
    try:
        with closing(conectar()) as con:
            return con.execute("SELECT * FROM livros WHERE id=?", (livro_id,)).fetchone()
    except sqlite3.OperationalError:
        return None


@app.route("/livro/<int:livro_id>")
def livro(livro_id):
    r = buscar_livro(livro_id)
    if not r:
        abort(404)
    return render_template("livro.html", livro=livro_dict(r), espera=ESPERA)


# ---------------------------------------------------------------- download com espera
# O token é assinado e carrega a hora em que foi criado: não precisa de banco nem de disco.
assinador = URLSafeTimedSerializer(SECRET_KEY, salt="download")


@app.route("/preparar/<int:livro_id>/<formato>", methods=["POST"])
def preparar(livro_id, formato):
    if formato not in FORMATOS or not buscar_livro(livro_id):
        abort(400)
    return jsonify({"token": assinador.dumps([livro_id, formato]), "espera": ESPERA})


@app.route("/baixar/<token>")
def baixar(token):
    try:
        (livro_id, formato), emitido = assinador.loads(
            token, max_age=ESPERA + TOKEN_VALIDADE, return_timestamp=True)
    except SignatureExpired:
        abort(410)
    except BadSignature:
        abort(404)
    if formato not in FORMATOS or not buscar_livro(livro_id):
        abort(404)

    decorrido = (datetime.now(timezone.utc) - emitido).total_seconds()
    if decorrido < ESPERA:
        return f"Aguarde mais {int(ESPERA - decorrido) + 1}s.", 429

    print(f"[download] livro={livro_id} formato={formato}")  # aparece nos logs da Vercel
    return redirect(FORMATOS[formato][1].format(id=livro_id))


# ---------------------------------------------------------------- estáticos
@app.route("/<arquivo>")
def estatico(arquivo):
    if arquivo in PAGINAS:
        return render_template(PAGINAS[arquivo])
    if arquivo not in ("style.css", "script.js"):
        abort(404)
    return send_from_directory(BASE, arquivo)


if __name__ == "__main__":
    app.run(debug=True)
