"""Campo de treinamento: ela pratica em charts sintéticos antes da guerra.

Roda o `SimWorker.tick()` de verdade — o mesmo código que joga o jogo real —
contra uma escada de charts cada vez mais difíceis, em tempo simulado e com
`dry_run=True`, então nunca encosta no teclado. Mesma regra do `tune.py`: nada
da decisão é reimplementado aqui, senão os números param de descrever a mosca.

A escada existe porque os modos de falha são diferentes e um esconde o outro:
velocidade testa o timing, brilho fraco testa se a regra de crista realmente
ignora amplitude, e o ruído de fundo testa ghost tap — que é o modo de falha
que o jogo real de verdade tem (palco animado, anúncio piscando ao lado).

    python3 train.py              # a escada inteira, um boletim por nível
    python3 train.py --rate 600   # a mesma escada, mas com o sim engasgando
    python3 train.py --selfcheck  # checagem rápida de que a bancada funciona

`--rate` existe porque no jogo real o painel mostrou SIM RATE entre 360 e 700
Hz, não os 1000 Hz de projeto — a máquina não dá conta do sim + captura +
overlay + Firefox ao mesmo tempo. Toda a temporização dela ERA por passo,
calibrada a 1 kHz, então rodar a 600 Hz esticava cada constante em 1,67x sem
ninguém avisar — e o pior: o próprio cérebro avançava 1 ms fixo por chamada,
ou seja, ela vivia 0,6 s de tempo-de-mosca por segundo do mundo, em câmera
lenta. Isso foi consertado (17/09): o `tick()` mede o intervalo real e o passa
pro `step()`, e a taxa é lida por segundo real. Rodar `--rate 600` agora tem
que dar o MESMO resultado que 1000 — se divergir, voltou a existir constante
por passo em algum lugar.

ATENÇÃO: ela NÃO aprende entre os níveis. Os pesos vêm do conectoma e são
fixos; o humor decai de volta a zero. Isso aqui mede a recruta, não treina —
treinar de verdade exige plasticidade, que ainda não existe no projeto.
"""
import sys

import numpy as np

import run
import tune

NOTES_PER_LEVEL = 20
SHIMMER_HOLD_S = 0.030   # quanto cada amostra de ruído dura — palco animado é
                         # ~30 Hz, não 1 kHz; ruído por passo seria filtrado fora

# nome, velocidade, brilho da nota, ruído de fundo
LEVELS = [
    ("1 aquecimento", 0.70, 1.00, 0.00),
    ("2 normal",      1.00, 1.00, 0.00),
    ("3 rapido",      1.40, 1.00, 0.00),
    ("4 nota fraca",  1.40, 0.70, 0.00),
    ("5 palco vivo",  1.40, 1.00, 0.35),
]


def play(speed, amp, noise, notes=NOTES_PER_LEVEL, seed=7, rate_hz=None):
    """Uma rodada. Retorna (sim, eventos julgados).

    `hit` nunca recebe ruído: é a verdade do juiz sobre quando a nota estava
    de fato em cima da seta. Sujar os dois faria o teste medir a si mesmo.

    `rate_hz` é quantos passos de simulação cabem em um segundo REAL. O tempo
    que o chart e o juiz enxergam anda sempre igual; o que muda é quantas vezes
    o cérebro pensa nesse meio tempo. É assim que o engasgo do jogo real entra
    na bancada sem mentir sobre a nota.
    """
    rng = np.random.default_rng(seed)
    seconds = notes * 0.9 / speed
    shared = {"drive": dict(run.ZERO_LANES), "hit": dict(run.ZERO_LANES),
              "status": "train"}
    sim = run.SimWorker(shared, demo=True, dry_run=True)

    events = []
    shimmer = dict(run.ZERO_LANES)
    next_shimmer = 0.0
    step_dt = 1.0 / (rate_hz or run.SIM_HZ)
    for i in range(int(seconds / step_dt)):
        t = i * step_dt
        px, hit_px = run.demo_pixels(t * speed)
        if amp != 1.0:
            px = {k: v * amp for k, v in px.items()}
        if noise:
            if t >= next_shimmer:
                # em segundos, não em passos: o palco pisca no tempo do mundo,
                # não na cadência do sim — e é justamente a diferença entre os
                # dois que este arquivo existe pra medir
                next_shimmer = t + SHIMMER_HOLD_S
                # abs: o capture lê |frame - fundo|, uma magnitude que nunca
                # é negativa — ruído centrado em zero seria fácil demais
                shimmer = {k: noise * run.NOTE_PIXELS * abs(rng.normal())
                           for k in run.ZERO_LANES}
            # soma em PIXEL, antes do piso: a câmera vê nota e anúncio como uma
            # variação só. Somar depois do piso deixaria o ruído entrar inteiro
            # e a bancada mediria uma mosca que não existe mais.
            px = {k: v + shimmer[k] for k, v in px.items()}
        drive = {k: run.pixels_to_drive(v) for k, v in px.items()}
        hit = {k: run.pixels_to_drive(v) for k, v in hit_px.items()}
        _, evs = sim.tick(t, drive, hit)
        events.extend(evs)
    return sim, events


def verdict(counts, notes=NOTES_PER_LEVEL):
    """Passa quem não perdeu nota, quase não bateu no vazio e acertou bonito."""
    if counts["Miss"]:
        return False, f"{counts['Miss']} nota(s) perdida(s)"
    if counts["Ghost"] > 1:
        return False, f"{counts['Ghost']} tapas no vazio"
    if counts["Sick"] < 0.8 * notes:
        return False, f"só {counts['Sick']}/{notes} Sick"
    return True, ""


def ladder(rate_hz=None):
    rate = rate_hz or run.SIM_HZ
    nota = "" if rate == run.SIM_HZ else f" · sim ENGASGANDO a {rate} Hz"
    print(f"{NOTES_PER_LEVEL} notas por nível · sem teclado · "
          f"sim {rate} Hz{nota}\n")
    passed = 0
    for name, speed, amp, noise in LEVELS:
        _, events = play(speed, amp, noise, rate_hz=rate_hz)
        counts, mean_delta = tune.summarize(events)
        ok, why = verdict(counts)
        passed += ok
        mark = "  PASSOU" if ok else f"  FALHOU ({why})"
        print(tune.line(name, counts, mean_delta) + mark)

    print(f"\n{passed}/{len(LEVELS)} níveis limpos.")
    if passed == len(LEVELS):
        print("pronta pro jogo real — o que sobrar de erro lá é captura, não decisão.")
    else:
        print("ainda não: o nível que falhou diz qual peça ajustar antes de armar o F9.")


def _demo():
    """Checagem: a bancada tem que julgar nota em toda velocidade da escada, e
    o nível de aquecimento — o mais fácil que existe — tem que passar. Se nem
    ele passa, quebrou a decisão, não a dificuldade."""
    for name, speed, amp, noise in LEVELS:
        _, events = play(speed, amp, noise, notes=6)
        counts, mean_delta = tune.summarize(events)
        graded = sum(counts[t] for t in ("Sick", "Good", "Bad", "Shit"))
        assert events, f"nível '{name}' não julgou evento nenhum"
        assert graded or counts["Miss"], f"nível '{name}' não produziu nota julgável"
        print(tune.line(name, counts, mean_delta))

    _, events = play(*LEVELS[0][1:], notes=6)
    ok, why = verdict(tune.summarize(events)[0], notes=6)
    assert ok, f"o aquecimento deveria passar sempre, mas: {why}"
    print("OK")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        _demo()
    elif "--rate" in sys.argv:
        ladder(rate_hz=int(sys.argv[sys.argv.index("--rate") + 1]))
    else:
        ladder()
