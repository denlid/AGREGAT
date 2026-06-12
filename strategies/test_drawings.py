from engine.api import Strategy

strategy = Strategy(
    name="Drawing Test All",
    risk=1.0,                     # not used, kept for form
    line_color="yellow",
    rect_color="blue",
    next_rect_color="green",
    text_color="white"
)

lastid = None

@strategy.on_candle()
def _(c):
    global lastid
    # 1. Draw a vertical line through the bar (low to high)
    line_id = strategy.draw_line(
        c.time, c.low+10,
        c.time, c.high,
        color=strategy.line_color,
        width=1,
        tag="vline"
    )

    # 2. Draw an unfilled rectangle around the bar
    rect_id = strategy.draw_rect(
        c.time, c.open_,
        c.time, c.close,
        color=strategy.rect_color,
        fill=True,
        tag="rect"
    )

    

    # 3. Draw text "X" at the close price level
    strategy.draw_text(
        c.time, c.close,
        "X",
        color=strategy.text_color,
        tag="text"
    )

    # 4. Update the rectangle: make it filled and green
    strategy.update_drawing(lastid, {"fill": True, "color": strategy.next_rect_color})
    lastid = rect_id
    # 5. Remove the vertical line immediately (it won't appear)
    strategy.remove_drawing(line_id)

    strategy.log("Drawing test: line removed by id, rect updated by id, text kept")
