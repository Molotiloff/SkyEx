from __future__ import annotations

from decimal import Decimal

from services.act_counter.models import ActCounterReport
from utils.formatting import format_amount_core


class ActCounterTextBuilder:
    def __init__(self, *, precision: int = 3) -> None:
        self.precision = precision

    def format_amount(self, amount: Decimal) -> str:
        return format_amount_core(amount, self.precision)

    def build_report_text(self, report: ActCounterReport) -> str:
        return "\n".join(
            [
                f"USDT ACT: <code>{self.format_amount(report.expected_amount)}</code>",
                "",
                f"База: <code>{self.format_amount(report.baseline_amount)}</code>",
                f"Приход: <code>+{self.format_amount(report.total_in)}</code>",
                f"Расход: <code>-{self.format_amount(report.total_out)}</code>",
                f"Ожидаем: <code>{self.format_amount(report.expected_amount)}</code>",
            ]
        )

    def build_current_amount_text(self, report: ActCounterReport) -> str:
        return f"USDT ACT: <code>{self.format_amount(report.expected_amount)}</code>"

    def build_statement_text(
        self,
        report: ActCounterReport,
        *,
        title: str,
    ) -> str:
        lines = [
            title,
            "",
            f"USDT ACT: <code>{self.format_amount(report.expected_amount)}</code>",
            "",
            f"База: <code>{self.format_amount(report.baseline_amount)}</code>",
            f"Приход: <code>+{self.format_amount(report.total_in)}</code>",
            f"Расход: <code>-{self.format_amount(report.total_out)}</code>",
            f"Ожидаем: <code>{self.format_amount(report.expected_amount)}</code>",
        ]

        if report.movements:
            lines.extend(["", "Движения:"])
            for movement in report.movements:
                sign = "+" if movement.direction == "IN" else "-"
                req_label = movement.table_req_id or movement.req_id
                lines.append(
                    f"{sign} <code>{self.format_amount(movement.amount)}</code> — заявка <code>{req_label}</code>"
                )
        else:
            lines.extend(["", "Движения: нет"])

        return "\n".join(lines)

    def build_reconcile_text(
        self,
        *,
        report: ActCounterReport,
        actual_amount: Decimal,
        delta: Decimal,
    ) -> str:
        lines = [
            f"ACT факт: <code>{self.format_amount(actual_amount)}</code>",
            f"ACT ожидание: <code>{self.format_amount(report.expected_amount)}</code>",
            f"Разрыв: <code>{self.format_amount(delta)}</code>",
            "",
            f"Новая точка отсчёта сохранена: <code>{self.format_amount(actual_amount)}</code>",
        ]

        return "\n".join(lines)
