# Fly Reflex — mosca real jogando FNF

A mosca-das-frutas (conectoma real do FlyWire) jogando Friday Night Funkin',
com o cérebro dela renderizado ao vivo na tela.

## Como rodar

A mosca começa DESARMADA: ela não encosta no teclado até você apertar **F9**.
F9 de novo — ou qualquer outra tecla — devolve o teclado na hora, sem fechar o
programa. O painel mostra quem está com o teclado (`KEYBOARD: YOURS / FLY`).

```sh
./start.sh --demo    # só assistir o cérebro, sem jogo e sem apertar tecla
./start.sh           # abre o FNF e a mosca joga de verdade
./start.sh --watch   # abre o jogo, mostra as decisões, mas não aperta nada
./start.sh --portal  # uma vez só: autoriza compartilhar tela → captura fica
                     # silenciosa e em velocidade máxima (recomendado)
```

No `--demo`, **S** pausa e retoma as notas sintéticas (o cérebro continua
rodando de verdade, só para de receber estímulo) — o rodapé mostra `PAUSED`.
Pra medir timing sem abrir nada: `python3 tune.py` (varredura) ou
`python3 tune.py --selfcheck` (rápido, com as asserções).

Você ainda escolhe a música — a mosca lê notas caindo, não lê menu.
A janela é arrastável; `Esc` fecha.

## O que cada arquivo faz

| arquivo | papel |
|---|---|
| `start.sh` | ponto de entrada único: sobe os daemons, abre o jogo, roda tudo |
| `run.py` | as três threads (captura, simulação 1 kHz, Qt); `tick()` é a decisão inteira |
| `hotkey.py` | lê o evdev direto: F9 arma/desarma, qualquer outra tecla desarma |
| `judge.py` | transforma o tempo do acerto em dopamina, dor e pontos |
| `fly_brain.py` | a simulação LIF do circuito real (668 neurônios) |
| `overlay.py` | a janela: cérebro 3D + mosca + leituras em Hz |
| `lanes.py` | acha as 4 pistas na tela sozinho; coordenadas salvas como fallback |
| `pipewire_capture.py` | captura via portal Wayland (rápida, silenciosa) |
| `screen_capture.py` | captura via Spectacle (plano B, não precisa de permissão) |
| `tune.py` | bancada de calibração: roda o `tick()` real em tempo simulado e mede |
| `vitals.py` | exame de saúde: taxa de disparo por população vs. faixa da mosca real |
| `train.py` | campo de treino: escada de 5 níveis, `--rate` simula a máquina engasgando |
| `simulate_song.py` | gera `song_recording.json` para a visualização web em `artifact/` |


## Decisões importantes (não refazer do zero)

**Dados.** Repo `desktop-fly` (CC BY-NC 4.0) clonado em `desktop-fly/` —
`git clone https://github.com/DenisSergeevitch/desktop-fly.git` no commit
`32b0001`. Ele tem `.git` próprio, então neste repo entra como gitlink: o
commit fica fixado, mas os arquivos não estão versionados aqui. Se a pasta
sumir, re-clonar.
`data/circuit.json` = 668 neurônios reais, 18.968 sinapses reais, com posição
3D e lado (esquerdo/direito) de cada um. `data/brain_points.json` = 23.210
coordenadas de soma do cérebro inteiro (é o que desenha a silhueta real do
cérebro na overlay). Não precisa de token do neuPrint.

**Parâmetros da simulação** seguem o ponto de operação documentado pelo projeto
original (`desktop-fly/CLAUDE.md`): tau 20,4 ms, limiar 1,0, 0,0008 de corrente
por sinapse, refratário de 2 ms, e um `BASELINE` que deixa os neurônios logo
abaixo do limiar. Esse ponto é fino de propósito — não escalar o baseline
linearmente, ou populações inteiras emudecem.

**O passo NÃO é mais fixo em 1 ms (17/09).** `step(dt_s)` integra o intervalo
real que o `tick()` mediu, e o ruído escala por `sqrt(DT/dt)` pra manter a
difusão por unidade de tempo constante — sem isso a rede fica mais agitada só
porque a máquina ficou lenta. Antes ela avançava 1 ms fixo por chamada: a
361-670 Hz que a máquina entrega de verdade, isso significava viver 0,4-0,7 s
de tempo-de-mosca por segundo do mundo, **em câmera lenta**, reagindo a nota
que já passou. Conferido: o repouso dá 0,68-0,72 Hz a 1000, 600 e 400 Hz, e a
escada do `train.py` dá o mesmo resultado nas três taxas. Se `--rate 600`
divergir de `--rate 1000`, é porque voltou a existir constante por passo em
algum lugar.

**Ponto de operação do estímulo: `DRIVE_SCALE = 0,09`** (era `0,192`). O
`vitals.py` mostrou o que 0,192 fazia: os 314 neurônios visuais (LPLC2 + LC4)
TODOS entre 172 e 189 Hz, com 10 Hz de diferença entre o mais quieto e o mais
agitado — a camada inteira no teto do refratário, sem carregar informação. Não
era cérebro de mosca, era convulsão (o diagnóstico de fora, "ela está tendo um
AVC", estava certo). Em 0,09 o pico durante uma nota de verdade é 76 Hz, dentro
do que um LPLC2 real alcança sob looming, e a separação do repouso na camada
descendente continua 3,8x. 0,05 seria mais calmo ainda, mas deixa só 2,4x —
pouco chão pro portão pisar.

**A fibra gigante `DNp01` nunca vai disparar com este dado, e isso não é bug.**
Os dois DNp01 do subgrafo recebem entrada LÍQUIDA NEGATIVA (-457 e -433 em
sinapses assinadas; 1,7x mais inibição que excitação, e até o que vem direto de
LPLC2/LC4 soma -387). Medido: 0,0 Hz sob nota, em qualquer ganho e com estímulo
em pedaço do olho ou na retina inteira. Quem carrega o sinal de aperto são os
outros DNs de entrada positiva — `DNp04` (+686, +825), `DNa02` (+982, +985),
`DNp02`, `DNp11`, `DNg11`. O `output_idx` já usa os 24 DNs juntos, então isso
está honesto; só não conte história de "a fibra gigante dela dispara".

**Ruído** (`NOISE = 0.03`) foi varrido: 0,02 deixa o cérebro morto e 0,05+
afoga o sinal. Em 0,03 dá ~0,7 Hz em repouso contra ~17 Hz sob estímulo
(contraste de 25×). Repouso vivo importa: cérebro silencioso parece bug.

**Intensidade de looming** (`CALIBRATED_INTENSITY = 0.12`): abaixo de ~0,05
nada chega na camada descendente, acima de ~0,30 satura e o humor deixa de
importar. Foi esse erro que fez o teste dopamina-vs-dor dar 0,0 vs 0,0 na
primeira versão — a intensidade usada caía na zona morta.

**Toggle do teclado (F9).** Atalho do Qt não serve: só dispara com a overlay
focada, e o ponto é justamente retomar o teclado enquanto o *jogo* está na
frente. Wayland também não dá hotkey global pra cliente sem privilégio. Então
`hotkey.py` lê `/dev/input/event*` direto — o grupo `input` (que o override do
ydotool já configurou) basta. **O device virtual do ydotoold é excluído pelo
nome**: vigiá-lo faria cada tecla que a própria mosca injeta parecer um humano
digitando, e ela se desarmaria na primeira nota. Nada é registrado ou salvo; o
watcher só pergunta "isso foi F9, ou foi outra coisa".

**Dopamina e pontuação agora são medidas, não sorteadas.** O `judge.py` mede a
distância entre o momento em que a mosca apertou e o momento em que a nota
estava de fato em cima da seta, e a recompensa é uma função contínua desse erro
(acerto perfeito = +0,6; na borda da janela de 180 ms ≈ 0; nota não tocada =
-1,0). A versão antiga sorteava um valor de uma tabela fixa por faixa
(`rng.choice`) e o placar nunca diminuía — dois números que não respondiam ao
que a mosca fazia. O `simulate_song.py` usa o mesmo juiz, então o replay web e
o jogo ao vivo passaram a pontuar pela mesma régua. Ler o texto Sick/Good/Miss
do próprio FNF seria melhor ainda, mas exige OCR do placar.

**Duas medidas diferentes por pista.** O cérebro e o juiz perguntam coisas
distintas da mesma pista, e misturar as duas era o bug: a *deriva de looming*
(o que o cérebro vê chegando) é a média ponderada pela proximidade da seta, ao
quadrado; o *sinal de acerto* (o que o juiz julga) lê só a faixa onde a seta
mora. Antes o drive era média achatada dos ~170px da pista inteira, então uma
nota recém-entrada pesava igual a uma nota em cima da linha — a mosca cruzava o
limiar com a nota ainda a um terço de segundo e errava tudo. Com o peso por
proximidade ela passou de 0/15 para 15/15 no chart sintético.

**Como escolhe a tecla.** O `circuit.json` tem só UMA população de entrada
(looming) e UMA de saída (fuga) — não distingue esquerda de direita. Então
rodam **4 cópias independentes do mesmo circuito real** (mesmos pesos,
potencial de membrana separado), uma por pista; a que dispara mais forte leva
a tecla. É honesto e é o que dá 4 decisões a partir de um circuito que só sabe
dizer "foge AGORA".

**QUANDO ela aperta: na crista, não num limiar.** Era um limiar fixo de Hz
(`PRESS_THRESHOLD_HZ = 12`) e isso amarrava o *momento* do aperto à *força* do
sinal: sinal mais forte cruza a mesma linha mais cedo. Medido no `tune.py`,
ela apertava **105 ms adiantada** e só tirava Bad/Good. E tunar o limiar não
resolvia — os valores que davam 26/26 Sick ficavam a 95-99% do pico da curva,
ou seja, **um sinal 15% mais fraco fazia ela errar as 26 notas**. Agora a
regra é: dispara quando a taxa de disparo **para de subir e cai por 15 ms**
(`FALL_STEPS`), que é a resposta do próprio circuito para "chegou agora" e não
se move quando o sinal fica mais alto. Resultado medido: **26/26 Sick com o
drive variando de 0,70× a 1,30×**, delta +17 a +24 ms, 0 miss.

Três constantes sustentam isso, e nenhuma é crítica no tempo (é essa a graça):
`ARM_HZ = 3` é só um portão de ruído (medido: repouso da saída bate no máximo
1,7 Hz em 20 s de silêncio, nota faz 5,9 Hz); `FALL_S = 0,015` é quanto tempo
de queda conta como crista; `REARM_RISE = 1.3` é o quanto a taxa precisa subir
de volta pra valer como nota nova — sem ele a cauda decaindo da mesma nota
disparava de novo a cada cooldown (26 notas viravam 36 apertos, o excedente
tudo Ghost). `RATE_TAU` subiu de 0,08 para **0,20**: a regra lê a *forma* da
curva, então curva lisa crista limpa.

`ARM_HZ` era 5 e `FALL_STEPS` era 15 *passos*. O 5 pertencia ao cérebro
saturado (lá a nota fazia 10,6 Hz); com o ganho corrigido ele ficou ACIMA da
nota e ela parou de apertar. Varrido: `RATE_TAU 0,20` + `FALL_S 15 ms` é o
ótimo — 0,30 já custa 18 das 20 notas do nível de nota fraca.

**⚠️ Nota de SEGURAR (sustain) fica pior com essa regra.** Como a barra mantém
o sinal no alto, a crista só acontece quando ela **acaba**: medido, uma barra
de 1,00 s→2,20 s fez ela apertar em t=2,215 s. Já existe a correção conhecida
(disparar quando a taxa *para de subir* em vez de exigir queda, que pega o
começo do platô), mas ela foi testada e **piora as notas normais** (ghosts
voltam, drift de tempo 3→16 ms), então ficou de fora até sustain existir de
verdade. Detalhe em `NEXT_SESSION.md`.

**Otimização que viabilizou tempo real.** Só uns poucos neurônios disparam por
passo, então somar essas linhas da matriz bate um matmul 668×668 cheio em ~30×.
Sem isso rodava a ~330 Hz; com isso passa de 3.900 Hz, folgado para o 1 kHz.

**Captura.** `org.kde.KWin.ScreenShot2` (a via nativa, por pipe) recusa com
`NoAuthorized` para processos não confiáveis — abandonada. Spectacle em modo
daemon DBus (`spectacle -s`) dá 8,4 fps reaproveitando o processo, contra 2,3
fps chamando o binário toda vez — mas grava um PNG por frame e **dispara uma
notificação por captura**, o que enterra o jogo em pop-ups. Por isso o caminho
bom é o portal do Wayland (PipeWire): sem disco, sem pop-up, na taxa do
monitor. Ele pede permissão uma vez (`--portal`) e lembra via token guardado em
`~/.config/fly-reflex-screencast.json`.

**Injeção de tecla.** `ydotoold` roda como serviço de sistema (root) e cria o
socket `0600 root:root` — o grupo `input` libera `/dev/uinput`, **não** esse
socket. Fix já aplicado: override em
`/etc/systemd/system/ydotool.service.d/override.conf` com
`--socket-own=0:104 --socket-perm=0660` (104 = gid do grupo `input`). O cliente
precisa de `YDOTOOL_SOCKET=/tmp/.ydotool_socket` porque o binário procura em
`/run/user/1000/` por padrão. `xdotool`/`mss` não funcionam em Wayland.

**Pistas.** `lanes.autocalibrate()` acha a fileira de 8 setas na tela a cada
início (e de novo se as pistas ficarem 10 s mudas, que costuma significar
janela movida). A seta "down" vive coberta pelo cabelo dos personagens, então
ela é reconstruída pelo espaçamento das outras em vez de medida direto.

## Pendente / ideias

1. Detecção de nota é heurística de variância de pixel contra um fundo médio
   móvel — funciona, mas fundo de palco muito agitado pode disparar falso.
   Upgrade: casar com as cores reais do sprite de nota do mod.
2. O julgamento é medido pelo nosso próprio sinal de acerto (a nota em cima da
   seta), não lido do placar do FNF. Bate com o que o jogo cobra, mas se o mod
   usar uma janela de acerto diferente os dois divergem. Ler de verdade exigiria
   OCR do texto Sick/Good/Miss.
2b. `simulate_song.py` (o replay web) ainda tem regra de aperto PRÓPRIA
   (`max(output_rates) > 0.03`, a 30 Hz) — não é a regra de crista do jogo ao
   vivo. Os números dele não descrevem mais a mosca que joga. O `tune.py` usa o
   `tick()` de verdade justamente por isso.
3. O peso por proximidade, a regra de crista e a faixa de acerto foram
   validados no chart sintético (26/26 Sick, incluindo com o drive a 0,7× e
   1,3×) e no replay offline, mas **nunca contra o jogo real** — falta rodar
   `./start.sh` e conferir se a mosca acerta de verdade. O ponto fraco
   conhecido é a *detecção* (variância de pixel), não mais o *timing*.
4. Automatizar os cliques de menu via mouse do `ydotool` (discutido, não feito).
5. Ideia paralela engavetada: usar a mesma rede num mod de Isaac (precisaria
   reescrever em Lua ou fazer IPC por arquivo).
