# Copyright (C) 2017-20 ForgeFlow S.L.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html)

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class RmaAddStockMove(models.TransientModel):
    _name = "rma_add_stock_move"
    _description = "Wizard to add rma lines from pickings"

    @api.model
    def default_get(self, fields_list):
        res = super(RmaAddStockMove, self).default_get(fields_list)
        rma_obj = self.env["rma.order"]
        rma_id = self.env.context["active_ids"] or []
        active_model = self.env.context["active_model"]
        if not rma_id:
            return res
        assert active_model == "rma.order", "Bad context propagation"

        rma = rma_obj.browse(rma_id)
        res["rma_id"] = rma.id
        res["partner_id"] = rma.partner_id.id
        res["move_ids"] = False
        return res

    rma_id = fields.Many2one(
        comodel_name="rma.order", string="RMA Order", readonly=True, ondelete="cascade"
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner", string="Partner", readonly=True
    )
    move_ids = fields.Many2many(
        comodel_name="stock.move",
        string="Stock Moves",
        domain="[('state', '=', 'done')]",
    )
    show_lot_filter = fields.Boolean(
        string="Show lot filter?",
        compute="_compute_lot_domain",
    )
    lot_domain_ids = fields.Many2many(
        comodel_name="stock.production.lot",
        string="Lots Domain",
        compute="_compute_lot_domain",
    )

    @api.depends(
        "move_ids.move_line_ids.lot_id",
    )
    def _compute_lot_domain(self):
        for rec in self:
            rec.lot_domain_ids = rec.mapped("move_ids.move_line_ids.lot_id").ids
            rec.show_lot_filter = bool(rec.lot_domain_ids)

    lot_ids = fields.Many2many(
        comodel_name="stock.production.lot", string="Lots/Serials selected"
    )

    def select_all(self):
        self.ensure_one()
        self.write(
            {
                "lot_ids": [(6, 0, self.lot_domain_ids.ids)],
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Add from Stock Move"),
            "view_mode": "form",
            "res_model": self._name,
            "res_id": self.id,
            "target": "new",
        }

    def _prepare_rma_line_from_stock_move(self, sm, lot=False):
        if self.env.context.get("customer"):
            operation = (
                sm.product_id.rma_customer_operation_id
                or sm.product_id.categ_id.rma_customer_operation_id
            )
        else:
            operation = (
                sm.product_id.rma_supplier_operation_id
                or sm.product_id.categ_id.rma_supplier_operation_id
            )
        if not operation:
            operation = self.env["rma.operation"].search(
                [("type", "=", self.rma_id.type)], limit=1
            )
            if not operation:
                raise ValidationError(_("Please define an operation first"))

        if not operation.in_route_id or not operation.out_route_id:
            route = self.env["stock.location.route"].search(
                [("rma_selectable", "=", True)], limit=1
            )
            if not route:
                raise ValidationError(_("Please define an RMA route"))

        if not operation.in_warehouse_id or not operation.out_warehouse_id:
            warehouse = self.env["stock.warehouse"].search(
                [
                    ("company_id", "=", self.rma_id.company_id.id),
                    ("lot_rma_id", "!=", False),
                ],
                limit=1,
            )
            if not warehouse:
                raise ValidationError(
                    _("Please define a warehouse with a default RMA location")
                )
        product_qty = sm.product_uom_qty
        if sm.product_id.tracking == "serial":
            product_qty = 1
        elif sm.product_id.tracking == "lot":
            product_qty = sum(
                sm.move_line_ids.filtered(lambda x: x.lot_id.id == lot.id).mapped(
                    "qty_done"
                )
            )
        data = {
            "partner_id": self.partner_id.id,
            "reference_move_id": sm.id,
            "product_id": sm.product_id.id,
            "lot_id": lot and lot.id or False,
            "origin": sm.picking_id.name or sm.name,
            "uom_id": sm.product_uom.id,
            "operation_id": operation.id,
            "product_qty": product_qty,
            "delivery_address_id": sm.picking_id.partner_id.id,
            "rma_id": self.rma_id.id,
            "receipt_policy": operation.receipt_policy,
            "delivery_policy": operation.delivery_policy,
            "in_warehouse_id": operation.in_warehouse_id.id or warehouse.id,
            "out_warehouse_id": operation.out_warehouse_id.id or warehouse.id,
            "in_route_id": operation.in_route_id.id or route.id,
            "out_route_id": operation.out_route_id.id or route.id,
            "location_id": (
                operation.location_id.id
                or operation.in_warehouse_id.lot_rma_id.id
                or warehouse.lot_rma_id.id
            ),
        }
        return data

    @api.model
    def _get_existing_stock_moves(self):
        existing_move_lines = []
        for rma_line in self.rma_id.rma_line_ids:
            existing_move_lines.append(rma_line.reference_move_id)
        return existing_move_lines

    def add_lines(self):
        rma_line_obj = self.env["rma.order.line"]
        existing_stock_moves = self._get_existing_stock_moves()
        for sm in self.move_ids:
            tracking_move = sm.product_id.tracking in ("serial", "lot")
            if sm not in existing_stock_moves or tracking_move:
                if sm.product_id.tracking == "none":
                    data = self._prepare_rma_line_from_stock_move(sm, lot=False)
                    rma_line_obj.with_context(default_rma_id=self.rma_id.id).create(
                        data
                    )
                else:
                    for lot in sm.move_line_ids.mapped("lot_id").filtered(
                        lambda x: x.id in self.lot_ids.ids
                    ):
                        if lot.id in self.rma_id.rma_line_ids.mapped("lot_id").ids:
                            continue
                        data = self._prepare_rma_line_from_stock_move(sm, lot)
                        rma_line_obj.with_context(default_rma_id=self.rma_id.id).create(
                            data
                        )
        return {"type": "ir.actions.act_window_close"}
