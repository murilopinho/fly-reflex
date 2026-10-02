# Fly Reflex

A mosca-das-frutas jogando Friday Night Funkin' com o próprio cérebro.

Peguei o conectoma real da *Drosophila* ([FlyWire](https://flywire.ai)), simulei os neurônios como uma rede LIF a 1 kHz e liguei a visão dela à tela do jogo e a resposta motora ao teclado. As notas que caem viram estímulo visual, os neurônios disparam e a mosca aperta a seta. O cérebro aparece desenhado ao vivo numa overlay, por cima do jogo.

> 🚧 **Em andamento / engavetado.** No nível 5 de dificuldade ela ainda entra em "convulsão" (atividade descontrolada). Dá pra ver o diário completo, com o que já foi tentado, em [STATUS.md](STATUS.md).

## Rodar

```sh
git clone --recursive https://github.com/murilopinho/fly-reflex
cd fly-reflex
./start.sh --demo    # só o cérebro, sem jogo e sem teclado
./start.sh           # abre o FNF e a mosca joga (F9 arma e desarma)
```

Linux (Wayland/PipeWire), Python 3.

## Créditos

O circuito, os pontos do cérebro e o sprite vêm do [desktop-fly](https://github.com/DenisSergeevitch/desktop-fly), de Denis Sergeevitch (incluído como submódulo). Os dados do conectoma vêm do FlyWire.

---

*EN: The real fruit fly connectome (FlyWire) simulated as a spiking network, playing Friday Night Funkin' on its own, with its brain rendered live on screen. Work in progress.*
