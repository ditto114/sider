def calculate_relative_coordinates(region_w, region_h, match_center_x, match_center_y):
    """
    Calculates coordinates relative to the center of the region.
    The center of the region is considered (0, 0).
    X axis: Positive to the right.
    Y axis: Positive upwards (standard Cartesian).
    
    Args:
        region_w (int): Width of the minimap region.
        region_h (int): Height of the minimap region.
        match_center_x (int): X coordinate of the character center within the region.
        match_center_y (int): Y coordinate of the character center within the region.
        
    Returns:
        tuple: (x, y) relative coordinates.
    """
    center_x = region_w / 2
    center_y = region_h / 2
    
    rel_x = int(match_center_x - center_x)
    # Screen Y increases downwards. To make Up positive, we do (Center - Y)
    rel_y = int(center_y - match_center_y)
    
    return rel_x, rel_y
