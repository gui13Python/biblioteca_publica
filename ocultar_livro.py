"""
Tira um livro do site (por exemplo, após um pedido de remoção por direitos autorais).
Edita o arquivo ocultos.txt. Depois, envie o ocultos.txt para o git: a Vercel publica
de novo e o livro some da busca, da página dele e dos downloads.

    python ocultar_livro.py 1342 55752     # oculta esses livros (use o número que aparece na URL)
    python ocultar_livro.py --mostrar 1342 # volta a exibir
    python ocultar_livro.py --listar       # mostra os ocultos
"""
import sys
from pathlib import Path

ARQ = Path(__file__).parent / "ocultos.txt"
CABECALHO = "# Livros ocultos do site (um número por linha). Depois de editar, envie ao git.\n"


def ler():
    if not ARQ.exists():
        return set()
    ids = set()
    for linha in ARQ.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#")[0].strip()
        if linha.isdigit():
            ids.add(int(linha))
    return ids


def salvar(ids):
    ARQ.write_text(CABECALHO + "".join(f"{i}\n" for i in sorted(ids)), encoding="utf-8")


def main():
    args = sys.argv[1:]
    atuais = ler()
    if "--listar" in args:
        print("Livros ocultos:", sorted(atuais) or "nenhum")
        return
    ids = {int(a) for a in args if a.isdigit()}
    if not ids:
        print(__doc__)
        return
    if "--mostrar" in args:
        salvar(atuais - ids)
        print("Voltaram a aparecer:", sorted(ids))
    else:
        salvar(atuais | ids)
        print("Ocultados:", sorted(ids))
    print("Agora envie o ocultos.txt para o git.")


if __name__ == "__main__":
    main()
