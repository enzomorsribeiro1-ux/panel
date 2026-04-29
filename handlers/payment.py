from __future__ import annotations

import asyncio
import aiohttp
import logging
import json


# docs: http://2.58.56.219:8000/docs#/
PAYMENT_API_BASE = "http://2.58.56.219:8000"
MIN_TOPUP_AMOUNT = "50.0"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PaymentHandler:

    def __init__(
        self,
    ):
        self.api_base = PAYMENT_API_BASE

    async def get_eth_price(
        self,
    ):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_base}/prices"
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("eth_usd")
                    else:
                        logger.error(f"failed to get ETH price: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"error getting ETH price: {e}")
            return None

    async def create_wallet(
        self,
    ):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_base}/wallet/create"
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"failed to create wallet: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"error creating wallet: {e}")
            return None

    async def create_payment(
        self,
        amount,
        description="Balance top-up",
    ):
        try:
            if amount < float(MIN_TOPUP_AMOUNT):
                return {"error": f"minimum top-up amount is ${MIN_TOPUP_AMOUNT}"}

            payload = {
                "amount": amount,
                "currency": "USD",
                "description": description,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_base}/payment/create",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"failed to create payment: {response.status} - {error_text}"
                        )
                        return {"error": f"payment creation failed: {response.status}"}
        except Exception as e:
            logger.error(f"error creating payment: {e}")
            return {"error": f"payment creation error: {str(e)}"}

    async def check_payment_status(
        self,
        payment_id,
    ):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_base}/payment/{payment_id}/status"
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        return None
        except Exception as e:
            logger.error(f"error checking payment status: {e}")
            return None

    async def get_wallet_payments(
        self,
        wallet_id,
    ):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_base}/wallet/{wallet_id}/payments"
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"failed to get wallet payments: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"error getting wallet payments: {e}")
            return None

    def format_payment_info(
        self,
        payment_data,
        eth_price,
    ):
        try:
            payment_id = payment_data.get("payment_id", "N/A")
            address = payment_data.get("address", "N/A")
            amount_eth = payment_data.get("amount", 0)
            amount_usd = payment_data.get("amount", 0) * eth_price
            status = payment_data.get("status", "unknown")

            message = (
                f"💳 Payment Details\n\n"
                f"> Payment ID: `{payment_id}`\n"
                f"> Amount: ${amount_usd:.2f} USD | ETH: `{amount_eth:.8f}`\n"
                f"> Address: `{address}`\n"
                f"> Status: {status.upper()}\n\n"
                f"Send the exact ETH amount to the wallet address above.\n"
                f"Please click ‘Check Status’ only after your transaction has at least two confirmations.\n\n"
                f"DISCLAIMER: We are not responsible for anything that happens. "
                f"If you lose your funds because you sent the wrong amount, we will not be able to help you"
            )

            return message
        except Exception as e:
            logger.error(f"error formatting payment info: {e}")
            return "err formatting payment information."


payment_handler = PaymentHandler()
