// Contagem regressiva antes do download.
// O servidor também confere o tempo, então não dá para burlar pulando o JS.
(function () {
  const botoes = document.getElementById("botoes");
  if (!botoes) return;

  const livroId = botoes.dataset.livro;
  const caixa = document.getElementById("espera");
  const spanSeg = document.getElementById("segundos");
  const barra = document.getElementById("progresso");
  let ocupado = false;

  botoes.addEventListener("click", async (e) => {
    const btn = e.target.closest(".btn-download");
    if (!btn || ocupado) return;
    ocupado = true;
    // abre a nova aba AGORA (dentro do clique) para o navegador não bloquear;
    // quando a contagem acabar, ela é redirecionada para o livro
    const aba = window.open("", "_blank");
    if (aba) {
      aba.document.write(
        "<title>Aguarde...</title><body style='font-family:Georgia,serif;text-align:center;padding-top:20vh'>" +
        "<h2>Preparando seu livro...</h2><p>Esta aba abrirá o livro em instantes.</p></body>"
      );
    }
    document.querySelectorAll(".btn-download").forEach((b) => (b.disabled = true));

    try {
      const r = await fetch(`/preparar/${livroId}/${btn.dataset.formato}`, { method: "POST" });
      const { token, espera } = await r.json();

      caixa.hidden = false;
      let restante = espera;
      spanSeg.textContent = restante;
      barra.style.width = "0%";

      const timer = setInterval(() => {
        restante -= 1;
        spanSeg.textContent = Math.max(restante, 0);
        barra.style.width = ((espera - restante) / espera) * 100 + "%";

        if (restante <= 0) {
          clearInterval(timer);
          // pequena folga para garantir que o servidor já considera o tempo cumprido
          setTimeout(() => {
            const destino = `/baixar/${token}`;
            if (aba && !aba.closed) {
              aba.location.href = destino;
            } else {
              window.open(destino, "_blank") || (window.location.href = destino);
            }
            caixa.querySelector("p").textContent = "Download iniciado!";
            document.querySelectorAll(".btn-download").forEach((b) => (b.disabled = false));
            ocupado = false;
          }, 600);
        }
      }, 1000);
    } catch (err) {
      if (aba && !aba.closed) aba.close();
      alert("Erro ao preparar o download. Tente novamente.");
      document.querySelectorAll(".btn-download").forEach((b) => (b.disabled = false));
      ocupado = false;
    }
  });
})();
