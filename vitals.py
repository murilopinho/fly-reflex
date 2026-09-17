"""Exame de sangue da mosca: ela está jogando ou está em crise elétrica?

O `tune.py` mede se ela ACERTA. Este mede se ela está SAUDÁVEL — que é outra
pergunta, e foi a que ninguém estava fazendo. Um cérebro saturado ainda passa
na bancada de acerto (a regra de crista é imune à amplitude), mas perde toda a
margem de ruído: qualquer tremida de pixel vira "NOTA AGORA" nas quatro pistas.

    python3 vitals.py            # repouso, nota, e a margem de ruído

Faixas de referência: neurônio central de Drosophila fica em ~0,5-10 Hz parado;
neurônio visual de projeção (LPLC2/LC4) sob looming sobe a dezenas de Hz; a
fibra gigante (DNp01) dispara essencialmente UMA vez por fuga. Sustentar 100+ Hz
não é mosca, é convulsão.
"""
import numpy as np

import fly_brain as fb
import run

STEPS = 2000
# (mínimo, máximo) de Hz que ainda passa por fisiológico sob estímulo.
# A medição aqui é com a nota PARADA em cima — pior caso, pior que o jogo, onde
# a nota é uma rampa de 0,4 s (pico medido pelo tick() real: 76 Hz a 0,09 de
# drive). Neurônio LC/LPLC2 de verdade chega a dezenas de Hz e bate perto de
# 100 Hz em looming forte, então 100 é o teto. O que estava aqui antes era
# 172-189 Hz sustentado com TODOS os 314 visuais juntos: isso não é faixa alta,
# é a camada inteira no teto do refratário.
HEALTHY_DRIVEN = {"LPLC2": (5, 100), "LC4": (5, 100), "DNp01": (0, 20),
                  "DNa01": (0, 40), "DNa02": (0, 40), "outros": (0, 50)}


def measure(brain, intensity, steps=STEPS):
    """Hz por neurônio, e a taxa coletiva da camada descendente (o que arma a pista)."""
    brain.reset()
    per_neuron = np.zeros(brain.n)
    dn_pop = 0.0
    for _ in range(steps):
        brain.stimulate_input(intensity)
        s = brain.step()
        per_neuron += s
        dn_pop += s[brain.output_idx].mean()
    return per_neuron / (steps / 1000.0), dn_pop / steps * 1000.0


def by_population(brain, hz):
    """Agrupa por tipo real de neurônio; o que não tem faixa própria cai em 'outros'."""
    groups = {}
    for i, t in enumerate(brain.type_of):
        groups.setdefault(t if t in HEALTHY_DRIVEN else "outros", []).append(i)
    return groups


def arming_drive(brain, arm_hz, hi):
    """Menor drive cujo disparo coletivo das saídas já cruza o portão de ruído."""
    for d in np.arange(0.002, hi, 0.002):
        if measure(brain, float(d), steps=600)[1] >= arm_hz:
            return float(d)
    return hi


def report():
    brain = fb.FlyBrain()
    full = run.DRIVE_SCALE
    print(f"{brain.n} neurônios · nota cheia = drive {full:.3f} · portão ARM_HZ = {run.ARM_HZ} Hz\n")

    for label, intensity in (("PARADA (sem nota)", 0.0), ("NOTA EM CIMA", full)):
        hz, dn = measure(brain, intensity)
        print(f"--- {label}   drive {intensity:.3f}   saída coletiva {dn:.1f} Hz")
        print(f"{'população':<12}{'n':>5}{'Hz médio':>10}{'pico':>8}{'>100Hz':>8}   faixa")
        for name, idx in by_population(brain, hz).items():
            h = hz[idx]
            lo, hi = HEALTHY_DRIVEN[name]
            bad = intensity > 0 and h.mean() > hi
            print(f"{name:<12}{len(idx):>5}{h.mean():>10.1f}{h.max():>8.0f}"
                  f"{int((h > 100).sum()):>8}   {lo}-{hi} Hz {'⚠ ALTO' if bad else 'ok'}")
        print()

    arm = arming_drive(brain, run.ARM_HZ, full)
    # o caminho de `run.pixels_to_drive` resolvido ao contrário: o portão é
    # quem manda embaixo, o ganho é quem manda em cima
    pixels = max(run.CONTRAST_FLOOR, (arm / full) * run.NOTE_PIXELS)
    print(f"PORTÃO DE RUÍDO: pista só acorda com {pixels:.1f}/255 de variação "
          f"({pixels / 255 * 100:.1f}% do recorte); nota cheia é {run.NOTE_PIXELS:.0f}")
    print(f"  piso de contraste: {run.CONTRAST_FLOOR:.0f} px — abaixo disso, zero absoluto")
    print(f"  teto do piso: 15 px. Acima, a nota fraca (0,7x = 15,4 px) morre junto —")
    print(f"  medido: piso 11 já custa 18 das 20 notas do nível 4.")

    hz_rest, _ = measure(brain, 0.0, steps=1500)
    print(f"\nHUMOR: um Sick vale +0,55. Ele multiplica o que ela VÊ (x{1 + fb.MOOD_GAIN:.2f}),")
    print(f"  não baixa o limiar — pista vazia continua vazia por melhor que ela esteja.")
    return pixels


if __name__ == "__main__":
    brain = fb.FlyBrain()
    rest = measure(brain, 0.0, steps=500)[1]
    driven = measure(brain, run.DRIVE_SCALE, steps=500)[1]
    assert driven > rest, "estímulo tem que levantar a saída; a medição está quebrada"
    report()
