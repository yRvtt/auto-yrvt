# Arquitetura do Auto yRvt

Este documento explica como o projeto é organizado e onde mexer para adicionar
ou corrigir uma funcionalidade.

## Visão geral

```
run.py                     atalho de entrada
auto_yrvt/
  __main__.py              DPI awareness + inicialização
  core.py                  Windows, teclas, captura, perfis, macros, instâncias
  ui.py                    interface Tkinter e animações
  updater.py               auto-update via GitHub Releases
  resources/icon.ico       ícone do app e do exe
tests/
  test_core.py             testes sem abrir o jogo
docs/
  ARCHITECTURE.md          este documento
  screenshots/             imagens usadas no README
```

O `core.py` não importa Tkinter. Isso permite testar perfis, captura e macros
sem abrir uma janela e mantém a interface substituível.

## Fluxo principal

```
EnumWindows -> list_mir4_windows() -> Manager.refresh()
                                      |
                                      +-- Instance por HWND
                                            |
                                            +-- thread do agendador
                                            |     +-- modo (farm, missão, boss...)
                                            |     +-- extras (buff, macros)
                                            |
                                            +-- thread de busca de câmara
```

Cada `Instance` tem o seu próprio `hwnd`, perfil e eventos de parada/pausa.
Teclas e cliques são enviados com `PostMessage`, então o bot não precisa estar
em primeiro plano. A captura usa `PrintWindow` + `GetDIBits`.

### Por que PostMessage?

Muitos jogos usam Raw Input e ignoram mensagens postadas. O Auto yRvt funciona
melhor no cliente PC quando a janela aceita mensagens; caso contrário, use a
janela ativa e rode como administrador. Essa limitação é do jogo, não do app.

## Perfis e configuração

Arquivo: `%APPDATA%\AutoYrvt\state.json`

Na primeira execução:

1. se existir `%APPDATA%\MIR4Bot\state.json`, ele é copiado para a nova pasta;
2. senão, `%APPDATA%\MIR4Bot\salas.json` é importado para o perfil `default`;
3. o arquivo antigo não é apagado.

Cada perfil guarda:

```json
{
  "name": "conta1",
  "window": "Mir4G[1]",
  "mode": "farm",
  "mode_cfg": { "farm": { "interval": 4.0, "ultimate": true } },
  "extras": {
    "buff": { "on": true, "interval": 180.0, "keys": ["1", "2", "3"] },
    "doar_cla": { "on": false, "interval": 86400.0, "macro": [], "repeat": 1 }
  },
  "points": { "amovivel": [x, y, w, h], "nome": null, "entrar": null },
  "refs": { "xp1": "base64..." }
}
```

O vínculo é feito pelo título da janela (`Mir4G[0]`, `Mir4G[1]`, ...). O título
exato tem prioridade; perfis sem vínculo usam `default`.

## Agendador

O `Instance._loop` roda em uma thread e mantém:

- `next_mode`: quando o próximo ciclo do modo pode rodar;
- `next_extra`: quando cada extra pode rodar;
- `next_action`: cooldown curto para não misturar ações;
- `prefer_extra`: alterna modo e extras para nenhum morrer de fome.

O modo é exclusivo; buff e macros rodam entre os ciclos. Macros longas bloqueiam
o modo enquanto executam, o que é intencional.

## Macros

Uma macro é uma lista de passos:

```json
{"t": "click", "x": 0.25, "y": 0.62, "delay": 0.8}
{"t": "key", "k": "f", "delay": 0.3, "hold": 0.06}
```

`x`/`y` são frações da janela, então a macro continua válida se a janela mudar
de tamanho. O gravador usa `GetAsyncKeyState` e converte cliques para
coordenadas relativas. `F9` encerra a gravação.

## Interface

A UI é Tkinter puro, sem dependências visuais externas. Componentes próprios:

- `HoverButton`: transição de cor no hover;
- `ToggleSwitch`: knob animado;
- `StatusDot`: pulso quando a conta está rodando;
- `SlimScrollbar`: barra fina desenhada em Canvas;
- `DarkCombo`: combo escuro com menu próprio;
- `Toast`: aviso deslizante no canto.

A navegação usa frames colocados com `place` e uma animação curta de slide.
A janela tem fade-in; todos os loops de animação usam `after` e param quando o
app fecha.

## Adicionando um modo

1. Adicione a entrada em `MODES` e o intervalo padrão em
   `MODE_DEFAULT_INTERVAL` (`core.py`).
2. Implemente o comportamento em `Instance.run_mode`.
3. A UI cria os cards automaticamente a partir de `MODES`; se precisar de
   opção extra (como Ultimate), adicione no card em `_build_tasks`.
4. Adicione um teste em `tests/test_core.py` se a lógica for testável sem jogo.

## Adicionando um extra

1. Adicione o padrão em `default_extras`.
2. Adicione em `MACRO_TASKS` se for uma macro gravável.
3. Trate em `Instance.run_extra` e na UI de extras.
4. Rode `python -m unittest discover -s tests`.

## Build

`build.bat` gera `dist\AutoYrvt.exe` com PyInstaller onefile + `--uac-admin`.
O workflow `.github/workflows/release.yml` faz o mesmo em tags `v*` e anexa o
exe ao release.

## Limitações conhecidas

- Jogos com Raw Input podem ignorar `PostMessage`.
- `PrintWindow` pode retornar preto em janelas minimizadas ou protegidas.
- Macros guardam posições relativas; mudanças grandes de layout exigem gravar
  de novo.
- UIAutomation é usado só no launcher; o jogo continua por `PostMessage`.
