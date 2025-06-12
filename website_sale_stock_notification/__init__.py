from . import models


def pre_uninstall_hook(cr, registry):
    """
    Pre-uninstall hook to clean up custom_message field and its references
    before the module is uninstalled.
    """
    from odoo import api, SUPERUSER_ID
    import logging
    
    _logger = logging.getLogger(__name__)
    
    with api.Environment.manage():
        env = api.Environment(cr, SUPERUSER_ID, {})
        
        _logger.info("Starting pre-uninstall cleanup for website_sale_stock_notification")
        
        # 1. Remove field definition from ir.model.fields
        field_record = env['ir.model.fields'].search([
            ('model', '=', 'product.template'),
            ('name', '=', 'custom_message')
        ])
        
        if field_record:
            _logger.info("Removing ir.model.fields record for custom_message")
            field_record.unlink()
        
        # 2. Clean up translations
        cr.execute("""
            DELETE FROM ir_translation 
            WHERE name = 'product.template,custom_message'
        """)
        
        # 3. Remove the database column (this will be handled automatically by Odoo
        # when the model is reloaded, but we can do it explicitly for cleaner removal)
        cr.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='product_template' AND column_name='custom_message'
        """)
        
        if cr.fetchone():
            _logger.info("Removing custom_message column from product_template table")
            cr.execute("ALTER TABLE product_template DROP COLUMN custom_message CASCADE")
        
        _logger.info("Pre-uninstall cleanup completed for website_sale_stock_notification")
