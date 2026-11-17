# MIR4 Bot

Manda teclas direto pra janela do MIR4 com `PostMessage` — **não tira o foco**,
pode usar o PC normalmente com o jogo atrás de outras janelas.

## Uso

1. Baixe `MIR4Bot.exe` na aba [Releases](../../releases).
2. Abra o jogo, depois o exe (ele pede admin — necessário pro Windows entregar as mensagens).
3. Ligue os toggles: **Acordar** (R → Tab → PageDown x3 → F), **Auto-Farm**, **Auto-Buff**.
4. Se quiser conferir se o jogo aceita, use **Testar tecla** com `1`.

Se o jogo não reagir, ele lê teclado por Raw Input e ignora mensagens postadas.

## Câmaras (XP 1 / XP 2 / XP 3 / Anti-Demônio)

Clica no botão azul **Amovível** até o nome da sala bater com a referência salva.

Treinar uma vez (a ordem importa):

1. Abra a tela das câmaras no jogo.
2. **Ponto Amovível** → clique e em 3s ponha o mouse sobre o botão azul.
3. **Ponto Nome** → clique e em 3s ponha o mouse no **começo** do texto do nome da sala
   (ex: `[6F] Câmara de Experiência II`). O recorte é de 340x26 px a partir desse ponto.
4. **Ponto Entrar** → onde clicar pra entrar na sala depois de achar.
5. **Salvar** na linha da sala desejada **enquanto ela está na tela** (repita para cada uma).
6. **Procurar** na linha da sala. **Parar** interrompe.

Tudo fica salvo em `%APPDATA%\MIR4Bot\salas.json` — só treina uma vez.
A captura usa `PrintWindow`, então funciona com o jogo atrás de outras janelas (não minimizado).

## Atualização

O app checa sozinho o último release ao abrir; clique na versão no canto pra checar na hora.
Se houver versão nova, ele baixa, troca o exe e reinicia.

## Publicar uma versão nova

1. Suba a versão em `APP_VERSION` (`mir4_bot.py`) e em `updater.py` não muda nada.
2. `git add -A && git commit -m "v1.0.1" && git tag v1.0.1 && git push --tags`
3. A Action `.github/workflows/release.yml` compila e anexa o exe no release.
4. Os usuários recebem no próximo abrir (ou clicando na versão).

Build local: `build.bat` → `dist\MIR4Bot.exe`.
