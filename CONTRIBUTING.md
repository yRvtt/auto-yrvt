# Contribuindo com o Auto yRvt

Obrigado por querer ajudar. O projeto é pequeno e direto, então mudanças
pequenas e bem testadas têm prioridade.

## Antes de abrir um PR

1. Rode os testes:

   ```bat
   python -m unittest discover -s tests -v
   ```

2. Rode o app em modo dev:

   ```bat
   python run.py
   ```

3. Se mexeu na interface, teste em uma janela de 1280x720 e em uma tela
   maior. O layout precisa continuar utilizável.

4. Se mexeu em `PostMessage`, captura de tela ou macros, descreva o que testou
   de verdade no jogo. Não é possível automatizar todos esses testes.

## Estilo

- Python 3.10+.
- Mantenha `core.py` independente de Tkinter.
- Comentários explicam o **porquê**, não o óbvio.
- Não adicione dependências para algo que a biblioteca padrão resolve.
- Preserve a compatibilidade com `state.json` e com o antigo `salas.json`.

## Reportar bug

Abra uma issue com:

- versão do Auto yRvt;
- versão do Windows;
- resolução/janela do jogo;
- o que esperava e o que aconteceu;
- trecho do registro;
- se possível, um print.

Não cole dados pessoais, senhas, tokens ou caminhos com seu nome.
