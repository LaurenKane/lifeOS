/* PixelStrip — days you did something.
 *
 * A GitHub-contribution-graph shape with the guilt sanded out: fill = `did
 * at least one thing`, an empty day is a faint dot, and no red exists in
 * the palette it uses (Q25a). It counts actions completed and upkeep
 * receipts — not app opens, which reward checking rather than doing. */
import React from "react";
import type { PixelDay } from "./types";

const WEEKS_SHOWN = 15;

export const PixelStrip: React.FC<{ data: PixelDay[] }> = ({ data }) => {
  const byDate = React.useMemo(
    () => new Map(data.map((day) => [day.date, day.count] as const)),
    [data],
  );

  const cells = React.useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const cells: { key: string; count: number; isFuture: boolean }[] = [];
    const start = new Date(today);
    const totalCells = WEEKS_SHOWN * 7;
    // Aligned from SUNDAY, filling row by row, ending today.
    const dow = today.getDay();
    start.setDate(start.getDate() - (totalCells - 1 - (6 - dow)));
    for (let i = 0; i < totalCells; i++) {
      const day = new Date(start);
      day.setDate(start.getDate() + i);
      cells.push({
        key: day.toISOString().slice(0, 10),
        count: byDate.get(day.toISOString().slice(0, 10)) ?? 0,
        isFuture: day.getTime() > today.getTime(),
      });
    }
    return cells;
  }, [byDate]);

  return (
    <div
      role="img"
      aria-label={`days you did something, last ${WEEKS_SHOWN} weeks`}
      className="flex gap-[3px] px-6 pb-6 font-mono"
    >
      {Array.from({ length: 7 }, (_, row) => (
        <div key={row} className="flex flex-col gap-[3px]">
          {Array.from({ length: WEEKS_SHOWN }, (_, week) => {
            const cell = cells[week * 7 + row];
            if (!cell) {
              return <span key={keyFor(week, row)} className="rect" />;
            }
            return (
              <span
                key={keyFor(week, row)}
                title={`${cell.key} · ${cell.count} thing${cell.count === 1 ? "" : "s"}`}
                className={
                  "h-[9px] w-[9px] rounded-[2px] " +
                  (cell.isFuture
                    ? "opacity-0"
                    : cell.count > 0
                      ? "bg-ink"
                      : "bg-ink/10")
                }
              />
            );
          })}
        </div>
      ))}
    </div>
  );
};

const keyFor = (week: number, row: number): string => `p${week}-${row}`;
