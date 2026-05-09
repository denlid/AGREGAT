from core.strategy import Strategy  # Updated import for AGREGAT platform
import numpy as np
import talib

class MovingAverageCrossoverStrategy(Strategy):
    """
    Стратегія на основі кросоверу двох простих ковзних середніх (SMA) з фільтром за обсягами.

    Параметри:
    - fast_period (int): Період швидкої SMA (за замовчуванням 50)
    - slow_period (int): Період повільної SMA (за замовчуванням 200)
    - volume_multiplier (float): Множник для фільтра обсягів (за замовчуванням 1.5)
    """

    name = "SMA Crossover with Volume Filter"

    def __init__(self):
        # Ініціалізація параметрів стратегії
        self.fast_period = 50
        self.slow_period = 200
        self.volume_multiplier = 1.5

        # Змінні для зберігання стану стратегії
        self.position = None  # Поточна позиція ('long', 'short', None)
        self.previous_crossover = None  # Попередній кросовер ('golden', 'death', None)

    def on_start(self, ctx):
        """Викликається при старті стратегії"""
        # Підписуємося на необхідні дані
        ctx.subscribe(self.fast_period, self.slow_period)

        # Логування параметрів стратегії
        ctx.log(f"Стратегія запущена з параметрами: fast_period={self.fast_period}, "
                f"slow_period={self.slow_period}, volume_multiplier={self.volume_multiplier}")

    def on_candle(self, ctx):
        """Викликається при появі нової свічки"""
        # Отримуємо необхідні дані
        close_prices = ctx.close[-self.slow_period*2:]  # Беремо достатньо даних для розрахунку
        volumes = ctx.volume[-self.slow_period*2:]

        if len(close_prices) < self.slow_period:
            return  # Недостатньо даних для розрахунку

        # Розраховуємо ковзні середні
        fast_sma = talib.SMA(close_prices, timeperiod=self.fast_period)[-1]
        slow_sma = talib.SMA(close_prices, timeperiod=self.slow_period)[-1]

        # Розраховуємо середній обсяг за останні 20 періодів
        avg_volume = np.mean(volumes[-20:])
        current_volume = volumes[-1]

        # Визначаємо поточний кросовер
        current_crossover = None
        if fast_sma > slow_sma:
            current_crossover = 'golden'  # Золотий кросовер (покупка)
        elif fast_sma < slow_sma:
            current_crossover = 'death'  # Смертельний кросовер (продаж)

        # Генеруємо сигнали з урахуванням фільтра обсягів
        if (current_crossover == 'golden' and
            self.previous_crossover != 'golden' and
            current_volume > avg_volume * self.volume_multiplier):

            # Закриваємо коротку позицію, якщо вона є
            if self.position == 'short':
                ctx.close_position()

            # Відкриваємо довгу позицію
            ctx.buy_shares = ctx.calc_position_size(0.01)  # Ризикуємо 1% капіталу
            self.position = 'long'
            ctx.log(f"Відкриваємо довгу позицію. Ціна: {ctx.close[-1]}")

        elif (current_crossover == 'death' and
              self.previous_crossover != 'death' and
              current_volume > avg_volume * self.volume_multiplier):

            # Закриваємо довгу позицію, якщо вона є
            if self.position == 'long':
                ctx.close_position()

            # Відкриваємо коротку позицію
            ctx.sell_shares = ctx.calc_position_size(0.01)  # Ризикуємо 1% капіталу
            self.position = 'short'
            ctx.log(f"Відкриваємо коротку позицію. Ціна: {ctx.close[-1]}")

        # Оновлюємо попередній кросовер
        self.previous_crossover = current_crossover

    def on_order_filled(self, ctx, order):
        """Викликається при виконанні ордера"""
        if order.side == 'buy':
            ctx.log(f"Куплено {order.filled_quantity} акцій за ціною {order.avg_fill_price}")
        elif order.side == 'sell':
            ctx.log(f"Продано {order.filled_quantity} акцій за ціною {order.avg_fill_price}")

    def on_stop(self, ctx):
        """Викликається при зупинці стратегії"""
        # Закриваємо всі відкриті позиції
        if self.position:
            ctx.close_position()
            ctx.log("Стратегія зупинена. Всі позиції закриті.")