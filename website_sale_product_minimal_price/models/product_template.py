# Copyright 2019 Tecnativa - Sergio Teruel
# Copyright 2020 Tecnativa - Pedro M. Baeza
# Copyright 2021 Tecnativa - Carlos Roca
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models
from odoo.osv import expression


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def _get_product_subpricelists(self, pricelist):
        base_domain = pricelist._get_applicable_rules_domain(
            self, fields.Datetime.now()
        )
        domain = expression.AND(
            [
                base_domain,
                [("compute_price", "=", "formula"), ("base", "=", "pricelist")],
            ]
        )
        pricelist_data = self.env["product.pricelist.item"]._read_group(
            domain,
            groupby=["base_pricelist_id"],
            aggregates=["base_pricelist_id:array_agg"],
        )
        pricelist_ids = [item for line in pricelist_data for item in line[1]]
        return self.env["product.pricelist"].browse(pricelist_ids)

    def _get_variants_from_pricelist(self, pricelist):
        return self.env["product.pricelist.item"].search(
            [
                ("pricelist_id", "in", pricelist.ids),
                ("product_id.product_tmpl_id", "in", self.ids),
            ]
        )

    def _get_pricelist_variant_items(self, pricelist):
        res = self._get_variants_from_pricelist(pricelist)
        next_pricelists = self._get_product_subpricelists(pricelist)
        res |= self._get_variants_from_pricelist(next_pricelists)
        visited_pricelists = pricelist
        while next_pricelists:
            pricelist = next_pricelists[0]
            if pricelist not in visited_pricelists:
                res |= self._get_variants_from_pricelist(pricelist)
                next_pricelists |= self._get_product_subpricelists(pricelist)
                next_pricelists -= pricelist
                visited_pricelists |= pricelist
            else:
                next_pricelists -= pricelist
        return res

    def _get_cheapest_info(self, pricelist):
        """Get the variant with the lowest price for each template.

        For backwards compatibility, a `(product, add_qty, has_distinct_price)`
        tuple is returned when called on a single template, and a dict keyed by
        template id with such tuples as values when called on several ones.
        """
        products_by_template = {}
        products = self.env["product.product"]
        variant_items_by_template = {}
        empty_items = self.env["product.pricelist.item"]
        # Avoid compute prices when pricelist has not item variants defined
        pricelist_variant_items = self._get_pricelist_variant_items(pricelist)
        for item in pricelist_variant_items:
            template_id = item.product_id.product_tmpl_id.id
            variant_items_by_template[template_id] = (
                variant_items_by_template.get(template_id, empty_items) | item
            )
        for template in self:
            # Variants with extra price
            variants_extra_price = template.product_variant_ids.filtered("price_extra")
            variants_without_extra_price = (
                template.product_variant_ids - variants_extra_price
            )
            variant_items = variant_items_by_template.get(
                template.id,
                empty_items,
            )
            if variant_items:
                # Take into account only the variants defined in pricelist and one
                # variant not defined to compute prices defined at template or
                # category level. Maybe there is any definition on template that
                # has cheaper price.
                variants = variant_items.mapped("product_id")
                template_products = (
                    variants + (template.product_variant_ids - variants)[:1]
                )
            else:
                template_products = variants_without_extra_price[:1]
            template_products |= variants_extra_price
            products_by_template[template.id] = template_products
            products |= template_products
        # Compute all the needed prices in batch instead of one product at a time
        prices_by_qty = {
            qty: pricelist._get_products_price(products, qty) for qty in (1, 99999999)
        }
        result = {}
        for template in self:
            min_price = 99999999
            product_find = self.env["product.product"]
            add_qty = 0
            has_distinct_price = False
            for product in products_by_template[template.id]:
                for qty in (1, 99999999):
                    product_price = prices_by_qty[qty][product.id]
                    if product_price != min_price and min_price != 99999999:
                        # Mark if there are different prices iterating over
                        # variants and comparing qty 1 and maximum qty
                        has_distinct_price = True
                    if product_price < min_price:
                        min_price = product_price
                        add_qty = qty
                        product_find = product
            result[template.id] = (
                product_find,
                add_qty,
                has_distinct_price,
            )
        if len(self) == 1:
            return result[self.id]
        return result

    def _get_first_possible_combination(
        self, parent_combination=None, necessary_values=None
    ):
        """Get the cheaper product combination for the website view."""
        res = super()._get_first_possible_combination(
            parent_combination=parent_combination, necessary_values=necessary_values
        )
        context = self.env.context
        if context.get("website_id") and self.product_variant_count > 1:
            # It only makes sense to change the default one when there are
            # more than one variants and we know the pricelist
            current_website = self.env["website"].get_current_website()
            pricelist = current_website.pricelist_id
            product = self._get_cheapest_info(pricelist)[0]
            # Rebuild the combination in the expected order
            res = self.env["product.template.attribute.value"]
            for line in product.valid_product_template_attribute_line_ids:
                value = product.product_template_attribute_value_ids.filtered(
                    lambda x, line=line: x in line.product_template_value_ids
                )
                if not value:
                    value = line.product_template_value_ids[:1]
                res += value
        return res

    def _get_combination_info(
        self,
        combination=False,
        product_id=False,
        add_qty=1,
        parent_combination=False,
        only_template=False,
    ):
        combination_info = super()._get_combination_info(
            combination=combination,
            product_id=product_id,
            add_qty=add_qty,
            parent_combination=parent_combination,
            only_template=only_template,
        )
        if only_template and not product_id:
            return combination_info
        combination = combination or self.env["product.template.attribute.value"]
        if only_template:
            product = self.env["product.product"]
        elif product_id:
            product = self.env["product.product"].browse(product_id)
            if combination - product.product_template_attribute_value_ids:
                # If the combination is not fully represented in the given product
                #   make sure to fetch the right product for the given combination
                product = self._get_variant_for_combination(combination)
        else:
            product = self._get_variant_for_combination(combination)
        if not product:
            # If no product is found, return the combination info without prices
            # the combination is not valid for the product or the product is archived
            return combination_info
        # Getting all min_quantity of the current product to compute the possible
        # price scale.
        qty_list = self.env["product.pricelist.item"].search(
            [
                "|",
                ("product_id", "=", product.id),
                "|",
                ("product_tmpl_id", "=", product.product_tmpl_id.id),
                (
                    "categ_id",
                    "in",
                    list(map(int, product.categ_id.parent_path.split("/")[0:-1])),
                ),
                ("min_quantity", ">", 0),
            ]
        )
        qty_list = sorted(set(qty_list.mapped("min_quantity")))
        price_scale = []
        last_price = product.with_context(quantity=0)._get_contextual_price()
        for min_qty in qty_list:
            new_price = product.with_context(quantity=min_qty)._get_contextual_price()
            if new_price != last_price:
                price_scale.append(
                    {
                        "min_qty": min_qty,
                        "price": new_price,
                        "currency_id": product.currency_id.id,
                    }
                )
                last_price = new_price
        combination_info.update(
            uom_name=product.uom_id.name,
            minimal_price_scale=price_scale,
        )
        return combination_info

    def _get_sales_prices(self, website):
        prices = super()._get_sales_prices(website)
        published_templates = self.filtered("is_published")
        if not published_templates:
            return prices
        pricelist = website.pricelist_id
        cheapest_info = published_templates._get_cheapest_info(pricelist)
        if len(published_templates) == 1:
            cheapest_info = {
                published_templates.id: cheapest_info,
            }
        currency = website.currency_id
        fiscal_position = website.fiscal_position_id.sudo()
        date = fields.Date.context_today(self)
        # Compute the prices of the cheapest variants in batch, grouping them by
        # the quantity that makes them the cheapest ones
        products_by_qty = {}
        for product, add_qty, _has_distinct_price in cheapest_info.values():
            if not product:
                continue
            products_by_qty.setdefault(add_qty, self.env["product.product"])
            products_by_qty[add_qty] |= product
        price_rules = {}
        for qty, qty_products in products_by_qty.items():
            price_rules.update(pricelist._compute_price_rule(qty_products, qty))
        for template in published_templates:
            product, add_qty, has_distinct_price = cheapest_info[template.id]
            if not product:
                continue
            pricelist_price, pricelist_rule_id = price_rules[product.id]
            # Mimic the standard `list_price` computation done in
            # `_get_additionnal_combination_info` for the cheapest variant
            price_before_discount = pricelist_price
            pricelist_item = self.env["product.pricelist.item"].browse(
                pricelist_rule_id
            )
            if pricelist_item._show_discount_on_shop():
                price_before_discount = pricelist_item._compute_price_before_discount(
                    product=product,
                    quantity=add_qty or 1.0,
                    date=date,
                    uom=product.uom_id,
                    currency=currency,
                )
            product_taxes = product.sudo().taxes_id._filter_taxes_by_company(
                self.env.company
            )
            taxes = fiscal_position.map_tax(product_taxes)
            price = self._apply_taxes_to_price(
                max(pricelist_price, price_before_discount),
                currency,
                product_taxes,
                taxes,
                product,
                website=website,
            )
            prices[template.id].update(
                distinct_prices=has_distinct_price,
                price=price,
            )
        return prices
