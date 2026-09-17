# Fly Reflex — pauta de debug

Lista levantada em conversa (15/09) e trabalhada na sessão de debug do mesmo
dia. Ler `STATUS.md` primeiro (fonte da verdade dos parâmetros calibrados) e
`DEBUG_PLAN.md` (histórico). Costume do Murilo: **primeiro diagnosticar sem
mexer no código, só corrigir depois de combinar.**

---

## 1. Setinhas visuais ✅ feito

Cada pista ganhou uma seta apontando pra sua direção, ao lado do nome. Ela
**preenche** quando o juiz diz que tem nota em cima da linha agora, e ganha um
**anel branco** no instante em que a mosca aperta. Seta acesa + anel ao mesmo
tempo = acerto que dá pra ver; anel em seta apagada = ela bateu no vazio.

O sinal `hit` do juiz passou a viajar até a overlay como `note_here` no
`snapshot()` de `run.py`; quem desenha é `arrow_polygon()` + `_paint_lanes()`
em `overlay.py`.

---

## 2. Trava em Bad/Good, quase nunca Sick ✅ resolvido — e o diagnóstico anterior estava invertido

**O que a sessão anterior escreveu aqui:** que os 80 ms de suavização
(`RATE_TAU`) atrasavam o aperto, e que a correção era **baixar** o `RATE_TAU`.

**O que a medição mostrou:** o contrário. Ela apertava **105 ms ADIANTADA**
(`delta` negativo). Baixar o `RATE_TAU` teria piorado. O erro do diagnóstico
antigo foi raciocinar sobre o atraso do filtro sem medir de que lado o erro
caía — daí o `tune.py` reportar `delta` **com sinal**, que é a única coisa que
distingue "cedo" de "tarde".

**A causa raiz não era a suavização, era o limiar fixo.** Um limiar de Hz
amarra o *momento* do aperto à *força* do sinal: sinal mais forte cruza a mesma
linha mais cedo. Pior, as configurações que davam 26/26 Sick só funcionavam
porque o limiar estava a 95-99% do pico da curva — **com o sinal 15% mais fraco
elas erravam as 26 notas**. Seria uma bomba-relógio no jogo real.

**Correção:** a regra virou detecção de crista (dispara quando a taxa cai por
`FALL_STEPS = 15` ms depois de subir), que é imune à amplitude. Medido:
**26/26 Sick com o drive de 0,70× a 1,30×**, delta +17 a +24 ms, 0 miss.
Detalhe em `STATUS.md`. Ferramenta nova: `tune.py`.

---

## 3. Notas de segurar (sustain) — ainda não suportado, e agora tem um detalhe novo

Continua sem suporte, e **não dá pra fechar sem o jogo real** (precisa saber
como uma barra aparece no sinal de variância de pixel). Mas a sessão mediu uma
coisa que muda o plano:

**A regra de crista piora o caso da barra.** Como o sustain mantém o sinal no
alto, a crista só acontece quando a barra **acaba** — medido: barra de 1,00 s a
2,20 s, ela apertou em **t = 2,215 s**, um piscar depois de já ter passado.

**A correção já foi escrita e testada, e foi revertida de propósito:** disparar
quando a taxa *para de fazer novos máximos* (em vez de exigir queda) pega o
começo do platô e resolve a barra — mas, medido nas notas normais, os ghosts
voltam e o drift de tempo sobe de 3 ms para 16 ms. Como a tecla ainda nem fica
segurada, o ganho era teórico e o prejuízo real. Fica guardado aqui pra quando
sustain for construído de verdade.

**Ordem certa quando for encarar:**
1. Rodar contra o jogo real e **gravar** o sinal de uma barra (é isso que falta;
   não dá pra calibrar às cegas).
2. Trocar a regra pelo "parou de subir" (já validada: pega o começo do platô).
3. Só então trocar `_press()` por manter `key {code}:1` e soltar no fim.

---

## 4. "Miss" não zerava combo ✅ resolvido — não era bug

Medido com a configuração antiga (`RATE_TAU 0.08`, limiar 12): em 26 notas deu
**0 Miss e 0 Ghost** — 21 Bad e 5 Good. Ou seja, o que aparecia na tela era
**"Bad"**, que não zera combo porque não deve mesmo zerar (é assim no FNF: só
Miss quebra combo). Nada quebrado, nenhuma linha alterada.

Com a regra nova o Bad sumiu (26/26 Sick), então a dúvida some junto.

Definições, pra não reabrir: **Miss** = tinha nota e ela não apertou a tempo
(ou errou por mais de 180 ms), zera combo. **Ghost** = apertou sem nota na
linha, não zera combo, de propósito. **Bad** = acertou, mas feio.

---

## 5. Lembrete permanente: nunca testado contra o jogo real

Tudo continua validado só em chart sintético e replay offline. O timing agora
tem margem larga (aguenta o sinal variando quase o dobro), então o suspeito
nº 1 se ela errar no jogo real passou a ser a **detecção de nota** (variância
de pixel, `CaptureWorker.run`), não mais o timing do aperto.

---

## 6. Achados desta sessão que viraram dívida nova

- **`simulate_song.py` tem regra de aperto própria** (`max(output_rates) > 0.03`
  a 30 Hz), diferente da regra de crista do jogo ao vivo. O replay web já não
  descreve a mosca que joga. Foi exatamente esse tipo de divergência que fez o
  `tune.py` ser construído em cima do `tick()` real.
- **O projeto não está em git.** Todo o código mora só em `~/fly-brain/`, e
  este projeto já foi perdido uma vez por existir só num `/tmp`. Um `git init`
  aqui custa dez segundos. **Agora tem `train.py`, que só existe aqui.**

---

## Sessão 5 (15-16/09) — primeiro contato com o jogo real

### O que mudou no código

- **`train.py` (NOVO)** — campo de treino: escada de 5 níveis de dificuldade,
  20 notas cada, tempo simulado, `dry_run` (nunca encosta no teclado). Roda o
  `tick()` real, mesma regra do `tune.py`. `./start.sh --train`.
- **`run.py`** — punição por ociosidade: passados `IDLE_GRACE_S` (2 s) sem
  apertar nada, o humor cai exponencialmente (`IDLE_PAIN_TAU`, `IDLE_PAIN_PEAK`).
  Só conta armada ou em demo. Chega ao fundo do poço por volta de 7 s parada.
- **`run.py`** — bug corrigido na mesma sessão que nasceu: `idle_since` era
  semeado com `time.time()`, mas o `tune.py` tica a partir de t=0. Isso deixava
  `idle_s` negativo e **desligava a punição só na bancada** — o pecado que o
  `tune.py` existe pra impedir. Agora inicializa no primeiro tick.
- **`overlay.py`** — `raise_()` a cada 1 s. O KWin não honra
  `WindowStaysOnTopHint` de um cliente XWayland contra janela Wayland nativa
  (Firefox) depois que ela ganha foco.
- **`pipewire_capture.py`** — timeout do portal 90 s → 300 s. 90 s não dá tempo
  de um humano ver e clicar no diálogo de compartilhar tela; estourava e caía
  pro Spectacle (que aí volta a tirar foto por frame).
- **`start.sh`** — flags agora **combinam** (`MODES=" $* "`); antes só o
  primeiro argumento era lido e o segundo era silenciosamente descartado, então
  `--watch --portal` rodava sem o portal. Adicionado `--train`. E
  **`--kiosk` → `--new-window`**: kiosk é fullscreen exclusivo, que o KWin põe
  acima de qualquer janela "sempre no topo" — o overlay ficava invisível a
  execução inteira, e o usuário ficava preso sem barra de título.

### O achado que importa: o pânico é reproduzível offline

O nível 5 do `train.py` ("palco vivo" = ruído de pixel em todas as pistas, que
é o anúncio piscando do KBHGames) reproduz exatamente o comportamento visto no
jogo real — ela entra em pânico e martela o teclado:

```
5 palco vivo   Sick 10  Good 7  Bad 2  Shit 1  Miss 0  Ghost 94   delta +55 ms
```

**20 notas, 94 tapas no vazio, e nenhuma nota perdida.** Isso mata a
necessidade do jogo real pra debugar esse caso: 13 segundos simulados, sem
anúncio, sem teclado, sem risco.

**`Miss 0` em todos os cinco níveis.** O problema dela nunca foi deixar de
apertar — é apertar demais. Qualquer ajuste futuro que a deixe mais
trigger-happy anda na direção errada.

### Duas coisas diagnosticadas e NÃO corrigidas (faltou combinar)

1. **O humor é compartilhado entre as 4 pistas.** Em `run.py`, o laço de
   recompensa faz `for b in self.brains.values(): b.apply_judgment(...)` — um
   acerto numa pista levanta o humor das **quatro**, e humor alto baixa o
   limiar de disparo de todas juntas. É a hipótese nº 1 do pânico (bate com o
   relato: ela tirou um "Good" e só então saiu martelando). O conserto óbvio é
   aplicar só no cérebro da pista do evento. **Agora dá pra provar**: rodar
   `train.py` nível 5 antes e depois. Perguntado duas vezes, sem resposta.

2. **A punição por ociosidade tem o sinal invertido em relação à exploração.**
   Humor negativo *aumenta* o limiar (`fly_brain.py:106`, "hesitant when
   mood<0"), então ficar parada → dor → congela mais → fica mais parada. É
   espiral de paralisia, não a inquietação exploratória que o Murilo descreveu
   ("sem receber nada bom ela vai mexer em tudo pra achar o porquê"). Inverter
   é 1 linha, mas os dados acima dizem pra pensar duas vezes: o problema dela é
   disparo a mais.

### Ela não aprende — e provavelmente é a próxima pergunta

Os pesos vêm do conectoma e são **fixos**; o humor decai de volta a zero. A
recruta de hoje é idêntica à de daqui a mil notas. O `train.py` **mede**, não
treina. Aprendizado de verdade exigiria plasticidade modulada por recompensa —
que biologicamente é o mecanismo de dopamina no *mushroom body*, e que este
conectoma **não tem** (conferido: 0 neurônios PAM/PPL1/DAN nos 668 carregados;
o "mood" é um escalar escrito à mão, não dopamina simulada). Proposto, não
aprovado.

### ⚠️ O SIM NÃO RODA A 1 kHz NO JOGO REAL (16/09, achado novo)

O painel do vídeo de 16/09 mostra `SIM RATE` entre **361 e 670 Hz**, nunca os
1000 Hz de projeto. A máquina não dá conta de sim + captura + overlay + Firefox
junto. E **toda** a temporização dela é por passo, calibrada assumindo 1 kHz:

- `FALL_STEPS = 15` = "15 ms de queda" → a 600 Hz viram **25 ms reais**
- `alpha = (1/SIM_HZ)/RATE_TAU` → média móvel vira 0,33 s em vez de 0,20 s
- `MOOD_DECAY = 0.998` por passo → humor dura **1,67x mais**
- (o `PRESS_COOLDOWN_S` e o juiz usam `now` real, então NÃO esticam — é a
  mistura dos dois regimes que desalinha tudo)

Medido com `python3 train.py --rate 600`:

| nível       | 1000 Hz            | 600 Hz                    |
|-------------|--------------------|---------------------------|
| normal      | +17 ms, 20 Sick, 0 ghost | +36 ms, 16 Sick, 1 ghost |
| rapido      | +22 ms, 2 ghosts   | +38 ms, **9 ghosts**      |
| nota fraca  | +22 ms, 0 ghosts   | +33 ms, **10 ghosts**     |
| palco vivo  | +42 ms, 0 miss     | +74 ms, **1 miss**        |

O atraso **dobra** e aparece a primeira `Miss` da história da bancada.

**Por que ninguém tinha visto:** `tune.py` e `train.py` rodam em tempo simulado
e sempre entregam 1000 passos por segundo simulado. A bancada estava medindo
uma mosca que só existe quando a máquina está ociosa. O `--rate` do `train.py`
existe agora pra fechar esse buraco — **usar ele antes de tunar qualquer
constante de tempo**.

**Mas não é o buraco inteiro:** a 600 Hz ela ainda faz 16/20 Sick no nível
normal, e o jogo real deu 8% de acerto. Suspeito nº 2, com evidência nos logs:
`auto-calibration failed (found only 0 arrow-shaped runs); using saved lanes
from lanes.py` — se ela está lendo as pistas salvas em vez das que estão na
tela, está olhando os pixels errados, e isso pesa mais que timing. **Investigar
a calibração de pistas ANTES de mexer em constante de tempo.**

Caminhos possíveis pro sim rate (nenhum decidido): passar o dt real pro
`tick()` e escalar as constantes por ele; ou baixar `SIM_HZ` pra algo que a
máquina sustente e recalibrar; ou dar menos trabalho ao processo (a captura
pelo portal já ajudou).

### Pegadinhas de ambiente confirmadas nesta sessão

- Fullscreen **de verdade** (kiosk, F11, `requestFullscreen()` do player)
  sempre esconde o overlay no KWin. **Maximizar não** — `Meta+PgUp` deixa o
  jogo grande com o cérebro visível. É essa a saída.
- `Window Fullscreen` e `Window Resize` estão como `none` no
  `kglobalshortcutsrc`. O que funciona: `Alt+F3` (menu da janela),
  `Meta+PgUp` (maximizar), `Meta`+arrastar botão direito (redimensionar).
- O KBHGames roda **vários anúncios em vídeo em sequência** antes do jogo
  carregar, e um interstitial estático pode ocupar o player inteiro por mais de
  um minuto. Gravação de teste virou quase toda anúncio duas vezes.

---

## Sessão 6 (17/09) — o ponto de operação estava em convulsão

Ponto de partida: relato de fora ("é o número de spikes, ela nem está jogando,
está tendo um AVC") + os 8% do jogo real. O relato estava certo, e a causa era
outra do que parecia.

### O que foi medido antes de mexer em nada

Ferramenta nova: **`vitals.py`** — `python3 vitals.py`. O `tune.py` mede se ela
ACERTA; este mede se ela está SAUDÁVEL, por população de neurônio, contra a
faixa da mosca real. Foi ele que achou tudo abaixo.

Com `DRIVE_SCALE = 0,192` (o valor que estava no jogo) e uma nota na pista:

| população | n | Hz médio | pico | acima de 100 Hz |
|---|---|---|---|---|
| LPLC2 | 210 | **172,2** | 183 | **210 de 210** |
| LC4 | 104 | **188,9** | 192 | **104 de 104** |
| DNp01 (fibra gigante) | 2 | **0,0** | 0 | 0 |

A camada visual inteira no teto do refratário, com 10 Hz de diferença entre o
mais quieto e o mais agitado — 314 neurônios em uníssono, sem carregar
informação nenhuma. E o neurônio de fuga em ZERO. Repouso, esse, estava
saudável (0,7 Hz): o problema só existia sob estímulo.

Varredura de ganho (pico visual medido pelo `tick()` real, durante uma nota de
verdade, não em estímulo constante):

| DRIVE_SCALE | pico visual | separação do repouso na saída |
|---|---|---|
| 0,060 | 53 Hz | 2,4x |
| **0,090 ← escolhido** | **76 Hz** | **3,8x** |
| 0,120 | 99 Hz | 4,5x |
| 0,192 (antigo) | 150 Hz | 6,2x |

76 Hz é o que um LPLC2 real alcança em looming. Acima disso é convulsão; abaixo
sobra pouco chão pro portão de ruído.

### O gatilho do pânico: o humor fabricava notas

O achado que explica "ela tirou um Good e só então saiu martelando", com número.
O humor baixava o LIMIAR da camada descendente (`MOOD_GAIN` em cima de
`thresh`). Taxa da saída **em repouso, sem estímulo nenhum**, por humor:

| humor | saída em repouso |
|---|---|
| 0,0 | 0,8 Hz |
| +0,4 | 2,7 Hz |
| +0,6 | **4,3 Hz** |
| +1,0 | **7,8 Hz** |

Um Sick vale **+0,55**. Ou seja: uma nota bem acertada punha as pistas em 4 Hz
de puro nada — acima do portão de armar. Ela literalmente alucinava nota depois
de acertar. Pior: a recompensa ia para os QUATRO cérebros (a hipótese nº 1 da
sessão 5, perguntada duas vezes sem resposta), então um acerto numa pista
alucinava nas outras três. Os ghosts da bancada caíam justamente em pistas onde
o chart nunca mandou nota — foi isso que entregou o bug.

Dois consertos:
1. **Recompensa só na pista que ganhou** (`run.py`, laço de eventos).
2. **Humor multiplica o que ela VÊ, não o limiar** (`fly_brain.py`): mood +1 faz
   a nota chegar 1,35x mais forte, mood -1 faz 0,65x — dentro da faixa 0,7-1,3x
   que a regra de crista aguenta. Pista vazia continua vazia por melhor que ela
   esteja. É também o mecanismo honesto: dopamina na mosca modula ganho de
   sinapse, não despolariza o neurônio descendente até ele disparar sozinho.
   Ghosts do nível limpo: **19 → 0**.

### A câmera lenta: o cérebro não vivia em tempo real

O buraco que a sessão 5 abriu e não fechou. Não eram só as constantes de
suavização: `FlyBrain.step()` avançava **1 ms fixo por chamada**. Com o sim
entregando 361-670 Hz no jogo real, ela vivia 0,4-0,7 s de tempo-de-mosca por
segundo do mundo — reagindo a nota que já passou.

Consertado de ponta a ponta, tudo em segundos reais:
- `tick()` mede o intervalo (`dt`) e passa pro `step(dt)`;
- `alpha = dt / RATE_TAU` (era `(1/SIM_HZ)/RATE_TAU`);
- `FALL_STEPS = 15` passos virou `FALL_S = 0,015` s;
- `MOOD_DECAY = 0.998` por passo virou `MOOD_TAU = 0,5` s;
- a taxa instantânea é `/dt`, não `* SIM_HZ` — esse detalhe sozinho inflava
  toda leitura em 1,67x a 600 Hz e rendia 26 tapas no vazio num chart limpo;
- o ruído escala por `sqrt(DT/dt)`, senão a rede fica mais agitada só porque a
  máquina engasgou.

Resultado — **a escada dá o mesmo a 1000 e a 600 Hz**, o que nunca tinha
acontecido:

| nível | 1000 Hz | 600 Hz | 400 Hz |
|---|---|---|---|
| 1 aquecimento | 20 Sick, 0 gh, +17 ms | 19 Sick, 1 gh, +20 ms | 20 Sick, 6 gh, +21 ms |
| 2 normal | 20 Sick, 2 gh, +15 ms | 20 Sick, 2 gh, +20 ms | 19 Sick, 0 gh, +11 ms |
| 3 rapido | 20 Sick, 0 gh, +20 ms | 20 Sick, 0 gh, +19 ms | 20 Sick, 5 gh, +22 ms |
| 4 nota fraca | 19 Sick, 0 gh, +17 ms | 19 Sick, 0 gh, +21 ms | 20 Sick, 2 gh, +23 ms |

(na sessão 5 o atraso DOBRAVA de 1000 pra 600 e os ghosts iam de 0 a 10)

`tune.py --selfcheck`: 6/6 Sick em 0,7x / 1,0x / 1,3x, **0 ghost**, drift de
tempo de **3 ms** na faixa toda.

### Piso de contraste — entrou, mas não faz o que se esperava

`CONTRAST_FLOOR = 8` px (de 255) em `pixels_to_drive()`, o funil único pelo qual
o drive do cérebro E o sinal do juiz passam. Abaixo de 8 px de variação média, o
drive é ZERO absoluto. Antes, 3,2 px (1,3% do recorte) já armava a pista — daí
qualquer anúncio piscando ler como nota nas quatro pistas.

Duas lições medidas aqui:
- **Portão, não subtração.** Subtrair o piso e reescalar come a amplitude da
  nota fraca (0,7x = 15,4 px; o piso de 8 deletava metade) e ela voltou a errar
  toda nota fraca — exatamente a dependência de amplitude que a regra de crista
  existe pra matar.
- **O piso não resolve o nível 5, e não tem como resolver.** O ruído do nível 5
  vale 35% de uma nota e a nota fraca vale 70%: eles se sobrepõem em amplitude.
  Medido: piso 11 px já custa 18 das 20 notas do nível 4, e o nível 5 só cai de
  144 pra 102 ghosts. **8 é o teto.**

Também tentado e **revertido**: exigir que a pista esteja acordada por ≥60 ms
antes de aceitar a crista (nota mantém a pista acima do portão por ~123 ms,
piscada de 30 ms não). Melhora pouco o nível 5 (139 → 106) e custa 8 notas do
nível 4. Não paga. Fica registrado pra ninguém tentar de novo.

### O que continua em pé, e o que fazer a seguir

1. **O nível 5 (palco vivo) continua falhando: 141-172 ghosts, 0 Miss.** Atenção
   ao comparar com os 94 da sessão 5: **não é o mesmo teste**. O ruído dele
   agora nasce em PIXEL e passa pelo mesmo funil da câmera (antes era injetado
   em unidade de drive, o que isentava a bancada inteira do piso de contraste —
   a bancada continuaria descrevendo uma mosca que não existe mais).
2. **Separar nota de piscada exige o dado que falta: um frame real do jogo.**
   Amplitude não separa (provado acima) e tempo não separa (provado acima). O
   candidato é ESPAÇO: a nota é um blob localizado que desce a pista, a piscada
   é mudança espalhada pelo recorte. O `delta` por linha já é calculado em
   `CaptureWorker.run` — o discriminador seria exigir pico localizado no perfil
   de linhas em vez de mudança uniforme. **Mas isso não se calibra às cegas**,
   mesma situação do sustain.
3. **Calibração de pistas, consertada mas NÃO testada contra o jogo:** ela agora
   insiste a cada 2 s até achar a strumline, em vez de aceitar o fallback pra
   sempre. Antes calibrava no PRIMEIRO frame — que no KBHGames é anúncio —,
   falhava, caía nas coordenadas do modo kiosk (que o `start.sh` não usa mais) e
   nunca tentava de novo, porque o retry de 10 s exige as pistas mudas e o
   anúncio piscando mantinha `drive > 0.01` pra sempre. A faixa de busca também
   deixou de ser pixels fixos do kiosk e passou a ser 10%-70% da altura do
   frame. **Suspeito nº 1 dos 8%, e só rodando o jogo se confirma.**
4. **Ela continua sem aprender.** Pesos fixos, humor decai a zero. Nada mudou
   nisso e nada foi aprovado.
