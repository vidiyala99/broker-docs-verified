import { Check } from "@phosphor-icons/react";
import { STEPS, type StepIndex } from "./draft";

/**
 * Four named steps (taste-core: no "Step 1" labels; the numeral stays because the order matters).
 * Reached steps are buttons; Create is reached only through Generate.
 */
export function Stepper({
  step,
  reached,
  onGo,
  compact,
}: {
  step: StepIndex;
  reached: StepIndex;
  onGo: (s: StepIndex) => void;
  compact: boolean;
}) {
  return (
    <nav aria-label="Proposal steps" className={`stepper${compact ? " stepper--compact" : ""}`}>
      <ol>
        {STEPS.map((name, i) => {
          const idx = i as StepIndex;
          const current = idx === step;
          const done = idx < step || (idx <= reached && idx !== step && idx < 3);
          const clickable = !current && idx <= reached && idx < 3;
          const state = current ? "current" : done ? "done" : "todo";
          const inner = (
            <>
              <span className="stepper__mark" aria-hidden>
                {done ? <Check size={14} weight="bold" /> : i + 1}
              </span>
              <span className="stepper__name">{name}</span>
              <span className="sr-only">
                , step {i + 1} of {STEPS.length}
                {done ? ", done" : ""}
              </span>
            </>
          );
          return (
            <li key={name} className={`stepper__item is-${state}`} aria-current={current ? "step" : undefined}>
              {clickable ? (
                <button type="button" className="stepper__btn" onClick={() => onGo(idx)}>
                  {inner}
                </button>
              ) : (
                <span className="stepper__btn">{inner}</span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
