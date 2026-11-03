import { Check } from "@phosphor-icons/react";
import { STEPS } from "./draft";

/**
 * Named steps (taste-core: no "Step 1" labels; the numeral stays because the order matters).
 * Reached steps are buttons; the last step (Create, Check) is reached only through its action.
 * Shared by the proposal wizard (default steps) and the Policy Check wizard.
 */
export function Stepper<S extends number>({
  step,
  reached,
  onGo,
  compact,
  steps = STEPS,
  label = "Proposal steps",
}: {
  step: S;
  reached: S;
  onGo: (s: S) => void;
  compact: boolean;
  steps?: readonly string[];
  label?: string;
}) {
  const last = steps.length - 1;
  return (
    <nav aria-label={label} className={`stepper${compact ? " stepper--compact" : ""}`}>
      <ol>
        {steps.map((name, i) => {
          const current = i === step;
          const done = i < step || (i <= reached && i !== step && i < last);
          const clickable = !current && i <= reached && i < last;
          const state = current ? "current" : done ? "done" : "todo";
          const inner = (
            <>
              <span className="stepper__mark" aria-hidden>
                {done ? <Check size={14} weight="bold" /> : i + 1}
              </span>
              <span className="stepper__name">{name}</span>
              <span className="sr-only">
                , step {i + 1} of {steps.length}
                {done ? ", done" : ""}
              </span>
            </>
          );
          return (
            <li key={name} className={`stepper__item is-${state}`} aria-current={current ? "step" : undefined}>
              {clickable ? (
                <button type="button" className="stepper__btn" onClick={() => onGo(i as S)}>
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
