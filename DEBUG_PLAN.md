# Fly Reflex — diagnóstico e correções

Levantamento feito lendo `run.py`, `fly_brain.py`, `overlay.py`, `simulate_song.py`,
seguido das correções. Estado: **os dois problemas relatados foram corrigidos**;
o que falta está no fim.

---

## 1. Não tinha toggle — travava o teclado principal ✅ corrigido

### Diagnóstico
`SimWorker._press` injeta a tecla por `ydotool`, que escreve em `/dev/uinput` — um
teclado virtual **de sistema**, não escopado à janela do FNF. Enquanto a simulação
rodasse, qualquer pista acima do limiar disparava uma tecla real, com qualquer
janela em foco.

Não havia como desligar:
- o único handler era `Esc` em `overlay.py`, que só dispara com a **overlay focada**
  (evento Qt local ao widget) e ainda por cima fecha o programa inteiro;
- `--dry-run` era flag de start, não toggle;
- `self.dry_run` nunca era reatribuído depois do `__init__` — nenhum caminho de
  código mudava aquilo em runtime.

### O que foi feito
- **`hotkey.py` (novo)** — lê `/dev/input/event*` direto via `struct`, sem
  dependência nova. Atalho do Qt não resolveria: só funciona com a overlay focada,
  e o objetivo é retomar o teclado enquanto o *jogo* está na frente; Wayland não
  oferece hotkey global pra cliente sem privilégio. O grupo `input` já estava
  configurado pelo override do ydotool.
- **F9 arma/desarma** e **qualquer outra tecla desarma na hora** — então basta
  começar a digitar que a mosca solta o teclado, sem fechar nada.
- **Começa DESARMADA.** Antes ela tomava o teclado no instante em que o processo
  subia.
- O device virtual do ydotoold (`event12` nesta máquina) é **excluído pelo nome**.
  Vigiá-lo faria cada tecla injetada pela própria mosca parecer humano digitando, e
  ela se desarmaria na primeira nota. Tem teste pra isso.
- Nada é registrado: o watcher só pergunta "foi F9, ou foi outra coisa".
- A overlay mostra `KEYBOARD: YOURS` ou `FLY`.
- Se nenhum teclado for legível, avisa e sobe **armada** (comportamento antigo),
  porque senão não haveria como armar.

---

## 2. Pontuação e dopamina ✅ corrigido

### Diagnóstico
Dois problemas distintos:

**(a) No jogo ao vivo não existia pontuação nenhuma.** `apply_judgment()` existia em
`fly_brain.py` mas **nunca era chamado** por `run.py`. Nada lia julgamento, nada
somava no humor — o `mood` só decaía por `MOOD_DECAY`, então o medidor da overlay
ficava colado no zero pra sempre. Era decoração.

**(b) No `simulate_song.py` (offline) a recompensa era sorteada.** A faixa dependia
da precisão, mas dentro da faixa o valor saía de `rng.choice` numa tabela fixa
(`Sick 0.6 / Good 0.3 / Bad -0.2...`). E o placar **só somava**: um Miss nunca
descontava ponto, só derrubava o humor. Ou seja, respondendo direto à pergunta: não
era 100% RNG, mas também não era proporcional ao desempenho.

### O que foi feito
- **`judge.py` (novo)** — uma única implementação de julgamento, usada tanto pelo
  jogo ao vivo quanto pelo replay offline. Mede a distância entre o instante do
  aperto e o instante em que a nota estava em cima da seta; a recompensa é **função
  contínua** desse erro:

  | erro | tier | dopamina | pontos |
  |---|---|---|---|
  | 0 ms | Sick | +0,600 | +350 |
  | 45 ms | Sick | +0,450 | +275 |
  | 85 ms | Good | +0,317 | +208 |
  | 125 ms | Bad | +0,183 | +142 |
  | 165 ms | Shit | +0,050 | +75 |
  | não tocada | Miss | **-1,000** | **-100** |

  Os nomes de tier são só rótulo pra overlay — **nada** a jusante lê eles, é o valor
  contínuo que vale. Miss agora desconta ponto e quebra o combo.
- `run.py` chama `apply_judgment` nos 4 cérebros a cada nota resolvida. O medidor de
  humor finalmente se move por causa do que a mosca fez.
- `simulate_song.py` usa o mesmo juiz — a tabela de RNG saiu.
- Placar deixou de depender do humor. Ponto é desempenho; humor é estado fisiológico.
  Misturar os dois era o que fazia acerto ruim com humor alto valer mais que acerto
  bom com humor baixo.

### Bugs encontrados durante a correção
1. **`MOOD_DECAY` aplicado na taxa errada.** É uma constante por passo calibrada pro
   loop de 1 kHz, mas `simulate_song.py` roda a 30 Hz e aplicava ela crua — um humor
   ali durava **30x mais** que o mesmo humor na mosca ao vivo. Agora é convertido
   (`MOOD_DECAY ** (1000/30)`).
2. **A mosca errava 100% das notas, sempre ~0,31 s adiantada.** Só apareceu porque o
   juiz passou a medir. Causa raiz: o drive era a média achatada dos ~170 px da
   pista inteira, então uma nota recém-entrada pesava **igual** a uma nota em cima
   da linha, e o cérebro cruzava o limiar com a nota ainda a um terço de segundo.
   Corrigido pesando as linhas pela proximidade da seta, ao quadrado (que também é
   como a área angular de um objeto em rota de colisão cresce de verdade).
   Resultado no chart sintético: **0/15 → 15/15**.
3. **Cérebro e juiz precisam de medidas diferentes da mesma pista.** O cérebro
   pergunta "tem nota chegando?" (crop inteiro, ponderado); o juiz pergunta "tem
   nota em cima da seta agora?" (só a faixa da seta). Usar o mesmo sinal pros dois
   era o que ancorava o julgamento no lugar errado.
4. **Janela de acerto não valia nada.** A nota resolvia assim que os pixels
   sossegavam (~30 ms após o pico), então `HIT_WINDOW_S = 180 ms` era ficção para
   presses atrasados. Agora a nota fica aberta até a janela expirar de fato.
5. **Ghost tap quebrava o combo.** Penalizava os disparos espontâneos do cérebro em
   repouso e enterrava uma corrida limpa de 51 notas num combo máximo de 3. O padrão
   do FNF (ghost tapping ligado) não cobra nada por bater no vazio; agora só custa
   um humor pequeno.

---

## Como verificar

```sh
python3 judge.py           # grading: dopamina cai conforme o tempo piora, miss desconta
python3 hotkey.py          # parser do evdev + confirma que o device do ydotool é excluído
python3 simulate_song.py   # replay offline: 51/51, 6 valores distintos de dopamina
python3 lanes.py           # detecção da strumline (já existia)
python3 fly_brain.py       # ponto de operação da simulação (já existia)
```

Resultado medido rodando o `SimWorker` real 14 s no chart sintético:
15 acertos, 0 erros, humor subindo até +0,32, e **12 valores distintos de dopamina em
15 notas** — que é a prova direta de que a recompensa não é nem fixa nem sorteada.

---

## Pendente

1. **Nada disso foi testado contra o jogo real.** O peso por proximidade, a faixa de
   acerto e o F9 foram validados no chart sintético, no replay offline e num render
   da overlay — mas ninguém rodou `./start.sh` com o FNF na frente. É o próximo
   passo e só você pode fazer.
2. O julgamento é medido pelo nosso próprio sinal, não lido do placar do FNF. Se o
   mod usar uma janela de acerto diferente da nossa (180 ms), os dois divergem.
   Ler de verdade exigiria OCR do texto Sick/Good/Miss.
3. Detecção de nota continua heurística de variância de pixel; fundo de palco muito
   agitado ainda pode dar falso positivo.
